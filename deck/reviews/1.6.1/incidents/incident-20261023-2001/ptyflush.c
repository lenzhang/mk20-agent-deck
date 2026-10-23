/* Diagnostic: tcflush(TCIFLUSH) on a tty (unstick a stalled pty input path). */
#include <fcntl.h>
#include <stdio.h>
#include <termios.h>
#include <unistd.h>
int main(int c, char **v) {
  if (c != 2) { fprintf(stderr, "usage: ptyflush /dev/pts/N\n"); return 2; }
  int fd = open(v[1], O_RDWR | O_NOCTTY | O_NONBLOCK);
  if (fd < 0) { perror("open"); return 1; }
  int r = tcflush(fd, TCIFLUSH);
  printf("tcflush=%d\n", r);
  close(fd); return r ? 1 : 0;
}
