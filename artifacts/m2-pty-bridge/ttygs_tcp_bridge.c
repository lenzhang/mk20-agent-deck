/* ttygs_tcp_bridge — make KeyboardDevice's /dev/ttyGS0 a PTY bridged to TCP.
 * Usage: ttygs_tcp_bridge --listen 127.0.0.1:17890 [--link /dev/ttyGS0]
 *        ttygs_tcp_bridge --connect 127.0.0.1:17890 [--link /tmp/ttyGS0.pty]
 * Host: adb forward tcp:17890 tcp:17890
 * Then Protocol A client talks to localhost:17890 instead of cu.usbmodem.
 * Built static armhf for Tina glibc device. */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <pty.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

static volatile sig_atomic_t g_run = 1;
static void on_sig(int s) { (void)s; g_run = 0; }

static int parse_hostport(const char *s, char *host, size_t hsz, int *port) {
  const char *colon = strrchr(s, ':');
  if (!colon) return -1;
  size_t n = (size_t)(colon - s);
  if (n >= hsz) return -1;
  memcpy(host, s, n); host[n] = 0;
  *port = atoi(colon + 1);
  return (*port > 0 && *port < 65536) ? 0 : -1;
}

static void pump(int a, int b) {
  char buf[4096];
  fd_set rfds;
  while (g_run) {
    FD_ZERO(&rfds);
    FD_SET(a, &rfds); FD_SET(b, &rfds);
    int m = (a > b ? a : b) + 1;
    int r = select(m, &rfds, NULL, NULL, NULL);
    if (r < 0) { if (errno == EINTR) continue; break; }
    if (FD_ISSET(a, &rfds)) {
      ssize_t n = read(a, buf, sizeof buf);
      if (n <= 0) break;
      if (write(b, buf, (size_t)n) != n) break;
    }
    if (FD_ISSET(b, &rfds)) {
      ssize_t n = read(b, buf, sizeof buf);
      if (n <= 0) break;
      if (write(a, buf, (size_t)n) != n) break;
    }
  }
}

static int bind_listen(const char *host, int port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) return -1;
  int yes = 1;
  setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof yes);
  struct sockaddr_in a; memset(&a, 0, sizeof a);
  a.sin_family = AF_INET; a.sin_port = htons((uint16_t)port);
  if (inet_pton(AF_INET, host, &a.sin_addr) != 1) { close(fd); return -1; }
  if (bind(fd, (struct sockaddr *)&a, sizeof a) < 0) { close(fd); return -1; }
  if (listen(fd, 1) < 0) { close(fd); return -1; }
  return fd;
}

static int dial(const char *host, int port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) return -1;
  struct sockaddr_in a; memset(&a, 0, sizeof a);
  a.sin_family = AF_INET; a.sin_port = htons((uint16_t)port);
  if (inet_pton(AF_INET, host, &a.sin_addr) != 1) { close(fd); return -1; }
  if (connect(fd, (struct sockaddr *)&a, sizeof a) < 0) { close(fd); return -1; }
  return fd;
}

int main(int argc, char **argv) {
  const char *mode = NULL, *addr = NULL, *linkpath = "/dev/ttyGS0";
  for (int i = 1; i < argc; i++) {
    if (!strcmp(argv[i], "--listen") && i + 1 < argc) { mode = "listen"; addr = argv[++i]; }
    else if (!strcmp(argv[i], "--connect") && i + 1 < argc) { mode = "connect"; addr = argv[++i]; }
    else if (!strcmp(argv[i], "--link") && i + 1 < argc) { linkpath = argv[++i]; }
    else { fprintf(stderr, "usage: %s --listen|-connect host:port [--link PATH]\n", argv[0]); return 2; }
  }
  if (!mode || !addr) { fprintf(stderr, "need --listen or --connect host:port\n"); return 2; }

  signal(SIGINT, on_sig); signal(SIGTERM, on_sig); signal(SIGPIPE, SIG_IGN);

  char host[64]; int port;
  if (parse_hostport(addr, host, sizeof host, &port) < 0) {
    fprintf(stderr, "bad addr %s\n", addr); return 2;
  }

  int master, slave;
  char slave_name[128];
  if (openpty(&master, &slave, slave_name, NULL, NULL) < 0) {
    perror("openpty"); return 1;
  }
  /* Unlock / expose as linkpath via symlink (bind-mount preferred when root). */
  unlink(linkpath);
  if (symlink(slave_name, linkpath) < 0) {
    fprintf(stderr, "symlink %s -> %s: %s (will print slave name)\n",
            linkpath, slave_name, strerror(errno));
  }
  fprintf(stderr, "pty slave=%s link=%s\n", slave_name, linkpath);
  /* Keep slave FD open so PTY stays alive even if KeyboardDevice closes briefly. */
  (void)slave;

  if (!strcmp(mode, "listen")) {
    int lfd = bind_listen(host, port);
    if (lfd < 0) { perror("listen"); return 1; }
    fprintf(stderr, "listening %s:%d\n", host, port);
    while (g_run) {
      int cfd = accept(lfd, NULL, NULL);
      if (cfd < 0) { if (errno == EINTR) continue; break; }
      fprintf(stderr, "client connected\n");
      pump(master, cfd);
      close(cfd);
      fprintf(stderr, "client gone; waiting again\n");
    }
    close(lfd);
  } else {
    int cfd = dial(host, port);
    if (cfd < 0) { perror("connect"); return 1; }
    pump(master, cfd);
    close(cfd);
  }
  close(master);
  unlink(linkpath);
  return 0;
}
