// PTY <-> TCP bridge for KeyboardDevice when ACM (ttyGS0) is not in the USB composite.
// Usage: ttygs_tcp_bridge [--dev IFACE] --listen ADDR:PORT [--listen ADDR:PORT ...] --link /dev/ttyGS0
// v2 (firmware 1.0.2): up to 4 --listen addresses (bind to the usb0 addresses only, e.g. the ECM IP and the
// link-local rescue alias, never 0.0.0.0); --dev pins every listener to that interface (SO_BINDTODEVICE), so a
// neighbour on another interface cannot reach the alias address; strict parsing (exit 2 on a bad argument).
// Creates a raw PTY, symlinks --link to its slave, serves one TCP client at a time (new client
// replaces old). With no client, output from KD is discarded so KD never blocks.
// v3 (firmware 1.2.0): --tap PATH serves a local unix socket (0600) that receives a copy of the KD->host bytes for
// the on-board router (mk20-router). One tap client at a time (new replaces old); sends are MSG_DONTWAIT and a full
// socket closes that tap client (r2: never a silent gap), so a slow or stuck tap client can never stall KD or the Mac link. Bytes from the tap
// client are read and discarded (it cannot write to KD). A tap that cannot be created is logged and skipped.
// v4 (firmware 1.6.1, incident 2026-10-23 on 1.6.0-rc4): the bridge never blocks its loop on the PTY master. The
// master is O_NONBLOCK; client->pty bytes go through one bounded pending buffer (PEND_MAX) written on POLLOUT, and the
// client is not read while that buffer holds bytes (TCP backpressure to the host, nothing dropped). If pending bytes
// make no write progress for PTY_STALL_MS the pty input path is stalled (kernel 5.4.61: the slave's input flip buffer
// was full while the slave line discipline was empty and its reader polled in vain; the old blocking write hung the
// whole loop for 51 min): tcflush(slave, TCIFLUSH) on the slave fd the bridge holds, one log line
// "pty input stalled N s: flushed (#k)", the pending bytes are discarded and the current client is closed (the host
// reconnects and re-hellos; the host protocol resyncs on its frame magic). KD->client forwarding, accepting and
// replacing clients and the tap keep working while the pty input is stalled.
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <poll.h>
#include <termios.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
static const char *linkp = "/dev/ttyGS0";
static const char *tapp = NULL;
static void cleanup(int s) { (void)s; unlink(linkp); if (tapp) unlink(tapp); _exit(0); }
static int tap_listen(const char *path) {  // -1 on failure (caller continues without a tap)
  struct sockaddr_un a; memset(&a, 0, sizeof a); a.sun_family = AF_UNIX;
  if (strlen(path) >= sizeof a.sun_path) return -1;
  strcpy(a.sun_path, path);
  int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
  if (fd < 0) return -1;
  unlink(path);
  mode_t old = umask(077);
  int rc = bind(fd, (void *)&a, sizeof a);
  umask(old);
  if (rc || chmod(path, 0600) || listen(fd, 1)) { close(fd); unlink(path); return -1; }
  return fd;
}
// v4: client->pty (to KD). PTY_STALL_MS: pending bytes the pty master has accepted none of for this long. Normal
// readers (mk20-kbd polls its host fd every <= 250 ms and reads up to 16 KiB per wake-up) drain the pty's ~64 KiB input
// in well under that; only a reader that is gone or a stalled kernel input path holds it for 2 s.
#define PTY_STALL_MS 2000
#define PEND_MAX 4096   // bounded: one client read; the client is not read again until it is written
static long long mono_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000; }
// Non-blocking write of the pending bytes. Returns bytes written (0 = pty full: wait for POLLOUT), -1 on a hard error.
static ssize_t wpend(int fd, const char *b, size_t *off, size_t len, long long *prog) {
  ssize_t done = 0;
  while (*off < len) {
    ssize_t w = write(fd, b + *off, len - *off);
    if (w > 0) { *off += (size_t)w; done += w; *prog = mono_ms(); continue; }
    if (w < 0 && errno == EINTR) continue;
    if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return done;
    return -1;
  }
  return done;
}
static int wsock(int fd, const char *b, ssize_t n) {  // non-blocking to the client: drop on EAGAIN so KD never stalls
  while (n > 0) { ssize_t w = send(fd, b, n, MSG_DONTWAIT | MSG_NOSIGNAL); if (w < 0) { if (errno == EINTR) continue; if (errno == EAGAIN || errno == EWOULDBLOCK) return 0; return -1; } b += w; n -= w; }
  return 0;
}
// tap (r120 GPT #1): an incomplete send (EAGAIN or partial) would silently lose a frame (e.g. a PTT release) on a
// connection the router still trusts, so the tap client is closed instead: the router sees EOF and releases all keys.
static int wtap(int fd, const char *b, ssize_t n) {
  for (;;) { ssize_t w = send(fd, b, n, MSG_DONTWAIT | MSG_NOSIGNAL); if (w < 0 && errno == EINTR) continue; return w == n ? 0 : -1; }
}
#define MAXL 4
static int parse(const char *s, struct sockaddr_in *a) {
  char host[64]; const char *c = strrchr(s, ':'); char *e;
  if (!c || c == s || (size_t)(c - s) >= sizeof host) return -1;
  snprintf(host, sizeof host, "%.*s", (int)(c - s), s);
  for (const char *q = c + 1; *q; q++) if (*q < '0' || *q > '9') return -1;   // digits only (no sign/space)
  if (!c[1] || strlen(c + 1) > 5) return -1;
  long port = strtol(c + 1, &e, 10);
  if (*e || port < 1 || port > 65535) return -1;
  memset(a, 0, sizeof *a); a->sin_family = AF_INET; a->sin_port = htons((unsigned short)port);
  if (inet_pton(AF_INET, host, &a->sin_addr) != 1) return -1;
  return a->sin_addr.s_addr == htonl(INADDR_ANY) ? -1 : 0;   // wildcard refused
}
int main(int argc, char **argv) {
  const char *ls_s[MAXL]; int nl = 0; const char *dev = NULL;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--listen") && i + 1 < argc) { if (nl == MAXL) { fprintf(stderr, "too many --listen\n"); return 2; } ls_s[nl++] = argv[++i]; }
    else if (!strcmp(argv[i], "--link") && i + 1 < argc) linkp = argv[++i];
    else if (!strcmp(argv[i], "--dev") && i + 1 < argc && strlen(argv[i + 1]) < 16) dev = argv[++i];
    else if (!strcmp(argv[i], "--tap") && i + 1 < argc && argv[i + 1][0] == '/' && strlen(argv[i + 1]) < 100) tapp = argv[++i];
    else { fprintf(stderr, "bad argument %s\n", argv[i]); return 2; }
  }
  if (!nl) { fprintf(stderr, "usage: ttygs_tcp_bridge --listen ADDR:PORT [--listen ...] --link PATH\n"); return 2; }
  struct sockaddr_in la[MAXL];
  for (int i = 0; i < nl; i++) if (parse(ls_s[i], &la[i])) { fprintf(stderr, "bad --listen %s\n", ls_s[i]); return 2; }
  signal(SIGPIPE, SIG_IGN); signal(SIGTERM, cleanup); signal(SIGINT, cleanup); signal(SIGHUP, SIG_IGN);
  int m = posix_openpt(O_RDWR | O_NOCTTY);
  if (m < 0 || grantpt(m) || unlockpt(m)) { perror("pty"); return 1; }
  if (fcntl(m, F_SETFL, fcntl(m, F_GETFL) | O_NONBLOCK)) { perror("pty nonblock"); return 1; }   // v4: never block the loop
  char *sn = ptsname(m);
  if (!sn) { perror("ptsname"); return 1; }
  int keep = open(sn, O_RDWR | O_NOCTTY); // hold slave open: no EIO on master when KD reopens
  struct termios t;
  if (keep < 0 || tcgetattr(keep, &t)) { perror("pty slave"); return 1; }
  cfmakeraw(&t); if (tcsetattr(keep, TCSANOW, &t)) { perror("tcsetattr"); return 1; }
  int one = 1, ls[MAXL];
  for (int i = 0; i < nl; i++) {  // bind every listener before touching the link, so a bad address leaves no dangling symlink
    ls[i] = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    setsockopt(ls[i], SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    if (dev && setsockopt(ls[i], SOL_SOCKET, SO_BINDTODEVICE, dev, strlen(dev) + 1)) { perror("SO_BINDTODEVICE"); return 1; }
    if (ls[i] < 0 || bind(ls[i], (void *)&la[i], sizeof la[i]) || listen(ls[i], 2)) { perror(ls_s[i]); return 1; }
  }
  unlink(linkp); if (symlink(sn, linkp)) { perror("symlink"); return 1; }
  for (int i = 0; i < nl; i++) fprintf(stderr, "bridge v4 %s <-> %s\n", sn, ls_s[i]);
  int tl = -1, tc = -1;
  if (tapp) { tl = tap_listen(tapp); if (tl < 0) { fprintf(stderr, "tap %s unavailable: %s (continuing without tap)\n", tapp, strerror(errno)); tapp = NULL; } else fprintf(stderr, "tap %s\n", tapp); }
  int cl = -1; char buf[4096];
  char pend[PEND_MAX]; size_t plen = 0, poff = 0; long long prog = 0; unsigned stalls = 0;   // client->pty pending bytes
  for (;;) {
    struct pollfd p[4 + MAXL]; int np = 0;
    p[np++] = (struct pollfd){m, (short)(POLLIN | (poff < plen ? POLLOUT : 0)), 0};
    for (int i = 0; i < nl; i++) p[np++] = (struct pollfd){ls[i], POLLIN, 0};
    int ci = -1; if (cl >= 0 && poff == plen) { ci = np; p[np++] = (struct pollfd){cl, POLLIN, 0}; }   // full: TCP backpressure
    int tli = -1, tci = -1;
    if (tl >= 0) { tli = np; p[np++] = (struct pollfd){tl, POLLIN, 0}; }
    if (tc >= 0) { tci = np; p[np++] = (struct pollfd){tc, POLLIN, 0}; }
    int to = -1;
    if (poff < plen) { long long left = prog + PTY_STALL_MS - mono_ms(); to = left < 0 ? 0 : (int)left; }
    if (poll(p, np, to) < 0) { if (errno == EINTR) continue; break; }
    if (tli >= 0 && (p[tli].revents & POLLIN)) {
      int n = accept4(tl, NULL, NULL, SOCK_CLOEXEC | SOCK_NONBLOCK);
      if (n >= 0) { if (tc >= 0) close(tc); tc = n; tci = -1; fprintf(stderr, "tap client connected\n"); }
    }
    if (tc >= 0 && tci >= 0 && (p[tci].revents & (POLLIN | POLLHUP | POLLERR | POLLNVAL))) {
      char junk[256]; ssize_t r = (p[tci].revents & POLLNVAL) ? -1 : recv(tc, junk, sizeof junk, MSG_DONTWAIT);
      if (r == 0 || (r < 0 && errno != EAGAIN && errno != EINTR)) { close(tc); tc = -1; fprintf(stderr, "tap client gone\n"); }
    }
    for (int i = 0; i < nl; i++) if (p[1 + i].revents & POLLIN) {
      int n = accept4(ls[i], NULL, NULL, SOCK_CLOEXEC);
      if (n >= 0) { if (cl >= 0) close(cl); cl = n; ci = -1; setsockopt(cl, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one); fprintf(stderr, "client connected via %s\n", ls_s[i]); }
    }
    if ((p[0].revents & (POLLERR | POLLNVAL)) && !(p[0].revents & POLLIN)) usleep(10000);   // no busy spin on a bad master
    if (p[0].revents & (POLLIN | POLLHUP)) {
      ssize_t r = read(m, buf, sizeof buf);
      if (r > 0 && cl >= 0 && wsock(cl, buf, r)) { close(cl); cl = -1; }
      if (r > 0 && tc >= 0 && wtap(tc, buf, r)) { close(tc); tc = -1; fprintf(stderr, "tap client dropped\n"); }
      if (r <= 0) usleep(10000);
    }
    if (poff < plen && (p[0].revents & (POLLOUT | POLLERR | POLLHUP))) {   // pending client bytes -> pty
      ssize_t w = wpend(m, pend, &poff, plen, &prog);
      if (w < 0) { fprintf(stderr, "pty write failed: %s (%zu pending bytes dropped)\n", strerror(errno), plen - poff); poff = plen; }
      else if (!w && (p[0].revents & POLLOUT)) usleep(10000);   // POLLOUT without room: no busy spin
      if (poff == plen) plen = poff = 0;
    }
    if (cl >= 0 && ci >= 0 && (p[ci].revents & (POLLIN | POLLHUP | POLLERR | POLLNVAL))) {
      ssize_t r = (p[ci].revents & POLLNVAL) ? -1 : recv(cl, pend, sizeof pend, MSG_DONTWAIT);
      if (r > 0) {
        plen = (size_t)r; poff = 0; prog = mono_ms();
        if (wpend(m, pend, &poff, plen, &prog) < 0) { fprintf(stderr, "pty write failed: %s (%zu pending bytes dropped)\n", strerror(errno), plen - poff); poff = plen; }
        if (poff == plen) plen = poff = 0;
      } else if (r == 0 || (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)) { close(cl); cl = -1; fprintf(stderr, "client gone\n"); }
    }
    if (poff < plen && mono_ms() - prog >= PTY_STALL_MS) {   // stalled pty input path: flush the slave, resync the host
      long long secs = (mono_ms() - prog) / 1000;
      int fr = tcflush(keep, TCIFLUSH);
      stalls++;
      fprintf(stderr, "pty input stalled %lld s: %s (#%u); %zu pending bytes dropped%s\n", secs,
              fr ? "flush FAILED" : "flushed", stalls, plen - poff, cl >= 0 ? ", client closed" : "");
      plen = poff = 0;
      if (cl >= 0) { close(cl); cl = -1; }
    }
    for (int i = 0; i < nl; i++) if (p[1 + i].revents & (POLLERR | POLLNVAL)) { fprintf(stderr, "listener %s failed\n", ls_s[i]); cleanup(0); }
  }
  cleanup(0); return 0;
}
