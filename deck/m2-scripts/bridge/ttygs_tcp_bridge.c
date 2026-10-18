// PTY <-> TCP bridge for KeyboardDevice when ACM (ttyGS0) is not in the USB composite.
// Usage: ttygs_tcp_bridge --listen ADDR:PORT [--listen ADDR:PORT ...] --link /dev/ttyGS0
// v2 (firmware 1.0.2): up to 4 --listen addresses (bind to the usb0 addresses only, e.g. the ECM IP and the
// link-local rescue alias, instead of 0.0.0.0); strict address/port parsing (exit 2 on a bad argument).
// Creates a raw PTY, symlinks --link to its slave, serves one TCP client at a time (new client
// replaces old). With no client, output from KD is discarded so KD never blocks.
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
static const char *linkp = "/dev/ttyGS0";
static void cleanup(int s) { (void)s; unlink(linkp); _exit(0); }
static int wall(int fd, const char *b, ssize_t n) {  // blocking-style (to the PTY / KD)
  while (n > 0) { ssize_t w = write(fd, b, n); if (w < 0) { if (errno == EINTR || errno == EAGAIN) { usleep(1000); continue; } return -1; } b += w; n -= w; }
  return 0;
}
static int wsock(int fd, const char *b, ssize_t n) {  // non-blocking to the client: drop on EAGAIN so KD never stalls
  while (n > 0) { ssize_t w = send(fd, b, n, MSG_DONTWAIT | MSG_NOSIGNAL); if (w < 0) { if (errno == EINTR) continue; if (errno == EAGAIN || errno == EWOULDBLOCK) return 0; return -1; } b += w; n -= w; }
  return 0;
}
#define MAXL 4
static int parse(const char *s, struct sockaddr_in *a) {
  char host[64]; const char *c = strrchr(s, ':'); char *e;
  if (!c || c == s || (size_t)(c - s) >= sizeof host) return -1;
  snprintf(host, sizeof host, "%.*s", (int)(c - s), s);
  long port = strtol(c + 1, &e, 10);
  if (*e || port < 1 || port > 65535) return -1;
  memset(a, 0, sizeof *a); a->sin_family = AF_INET; a->sin_port = htons((unsigned short)port);
  return inet_pton(AF_INET, host, &a->sin_addr) == 1 ? 0 : -1;
}
int main(int argc, char **argv) {
  const char *ls_s[MAXL]; int nl = 0;
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--listen") && i + 1 < argc) { if (nl == MAXL) { fprintf(stderr, "too many --listen\n"); return 2; } ls_s[nl++] = argv[++i]; }
    else if (!strcmp(argv[i], "--link") && i + 1 < argc) linkp = argv[++i];
    else { fprintf(stderr, "bad argument %s\n", argv[i]); return 2; }
  }
  if (!nl) ls_s[nl++] = "0.0.0.0:17890";
  struct sockaddr_in la[MAXL];
  for (int i = 0; i < nl; i++) if (parse(ls_s[i], &la[i])) { fprintf(stderr, "bad --listen %s\n", ls_s[i]); return 2; }
  signal(SIGPIPE, SIG_IGN); signal(SIGTERM, cleanup); signal(SIGINT, cleanup); signal(SIGHUP, SIG_IGN);
  int m = posix_openpt(O_RDWR | O_NOCTTY);
  if (m < 0 || grantpt(m) || unlockpt(m)) { perror("pty"); return 1; }
  char *sn = ptsname(m);
  int keep = open(sn, O_RDWR | O_NOCTTY); // hold slave open: no EIO on master when KD reopens
  struct termios t; tcgetattr(keep, &t); cfmakeraw(&t); tcsetattr(keep, TCSANOW, &t);
  int one = 1, ls[MAXL];
  for (int i = 0; i < nl; i++) {  // bind every listener before touching the link, so a bad address leaves no dangling symlink
    ls[i] = socket(AF_INET, SOCK_STREAM | SOCK_CLOEXEC, 0);
    setsockopt(ls[i], SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
    if (ls[i] < 0 || bind(ls[i], (void *)&la[i], sizeof la[i]) || listen(ls[i], 2)) { perror(ls_s[i]); return 1; }
  }
  unlink(linkp); if (symlink(sn, linkp)) { perror("symlink"); return 1; }
  for (int i = 0; i < nl; i++) fprintf(stderr, "bridge v2 %s <-> %s\n", sn, ls_s[i]);
  int cl = -1; char buf[4096];
  for (;;) {
    struct pollfd p[2 + MAXL]; int np = 0;
    p[np++] = (struct pollfd){m, POLLIN, 0};
    for (int i = 0; i < nl; i++) p[np++] = (struct pollfd){ls[i], POLLIN, 0};
    int ci = np; if (cl >= 0) p[np++] = (struct pollfd){cl, POLLIN, 0};
    if (poll(p, np, -1) < 0) { if (errno == EINTR) continue; break; }
    for (int i = 0; i < nl; i++) if (p[1 + i].revents & POLLIN) {
      int n = accept4(ls[i], NULL, NULL, SOCK_CLOEXEC);
      if (n >= 0) { if (cl >= 0) close(cl); cl = n; ci = -1; setsockopt(cl, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one); fprintf(stderr, "client connected via %s\n", ls_s[i]); }
    }
    if (p[0].revents & (POLLIN | POLLHUP)) {
      ssize_t r = read(m, buf, sizeof buf);
      if (r > 0 && cl >= 0 && wsock(cl, buf, r)) { close(cl); cl = -1; }
      if (r <= 0) usleep(10000);
    }
    if (cl >= 0 && ci >= 0 && ci < np && (p[ci].revents & (POLLIN | POLLHUP | POLLERR))) {
      ssize_t r = read(cl, buf, sizeof buf);
      if (r <= 0) { close(cl); cl = -1; fprintf(stderr, "client gone\n"); }
      else wall(m, buf, r);
    }
  }
  cleanup(0); return 0;
}
