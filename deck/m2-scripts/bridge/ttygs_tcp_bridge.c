// PTY <-> TCP bridge for KeyboardDevice when ACM (ttyGS0) is not in the USB composite.
// Usage: ttygs_tcp_bridge --listen ADDR:PORT --link /dev/ttyGS0
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
int main(int argc, char **argv) {
  const char *listen_s = "0.0.0.0:17890";
  for (int i = 1; i < argc - 1; i++) {
    if (!strcmp(argv[i], "--listen")) listen_s = argv[++i];
    else if (!strcmp(argv[i], "--link")) linkp = argv[++i];
  }
  char host[64]; int port = 17890; const char *c = strrchr(listen_s, ':');
  if (!c) return 2; snprintf(host, sizeof host, "%.*s", (int)(c - listen_s), listen_s); port = atoi(c + 1);
  signal(SIGPIPE, SIG_IGN); signal(SIGTERM, cleanup); signal(SIGINT, cleanup); signal(SIGHUP, SIG_IGN);
  int m = posix_openpt(O_RDWR | O_NOCTTY);
  if (m < 0 || grantpt(m) || unlockpt(m)) { perror("pty"); return 1; }
  char *sn = ptsname(m);
  int keep = open(sn, O_RDWR | O_NOCTTY); // hold slave open: no EIO on master when KD reopens
  struct termios t; tcgetattr(keep, &t); cfmakeraw(&t); tcsetattr(keep, TCSANOW, &t);
  unlink(linkp); if (symlink(sn, linkp)) { perror("symlink"); return 1; }
  int ls = socket(AF_INET, SOCK_STREAM, 0), one = 1;
  setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
  struct sockaddr_in a = {0}; a.sin_family = AF_INET; a.sin_port = htons(port); inet_pton(AF_INET, host, &a.sin_addr);
  if (bind(ls, (void *)&a, sizeof a) || listen(ls, 2)) { perror("listen"); return 1; }
  fprintf(stderr, "bridge %s <-> %s:%d\n", sn, host, port);
  int cl = -1; char buf[4096];
  for (;;) {
    struct pollfd p[3] = {{m, POLLIN, 0}, {ls, POLLIN, 0}, {cl, POLLIN, 0}};
    if (poll(p, cl >= 0 ? 3 : 2, -1) < 0) { if (errno == EINTR) continue; break; }
    if (p[1].revents & POLLIN) {
      int n = accept(ls, NULL, NULL);
      if (n >= 0) { if (cl >= 0) close(cl); cl = n; setsockopt(cl, IPPROTO_TCP, TCP_NODELAY, &one, sizeof one); fprintf(stderr, "client connected\n"); }
    }
    if (p[0].revents & (POLLIN | POLLHUP)) {
      ssize_t r = read(m, buf, sizeof buf);
      if (r > 0 && cl >= 0 && wsock(cl, buf, r)) { close(cl); cl = -1; }
      if (r <= 0) usleep(10000);
    }
    if (cl >= 0 && (p[2].revents & (POLLIN | POLLHUP | POLLERR))) {
      ssize_t r = read(cl, buf, sizeof buf);
      if (r <= 0) { close(cl); cl = -1; fprintf(stderr, "client gone\n"); }
      else wall(m, buf, r);
    }
  }
  cleanup(0); return 0;
}
