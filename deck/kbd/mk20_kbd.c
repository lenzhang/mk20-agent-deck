/* mk20-kbd (phase 1 skeleton): KD replacement daemon.
 * Implemented now:  --monitor   read /dev/ttyS1 RECEIVE-ONLY (O_RDONLY), log key events
 *                   --pattern   write an id test pattern to <fbdir>/fb1..fb21 (needs KD stopped)
 *                   --getinfo   print the getInfo JSON we will answer with
 * Not yet: host link on ttyGS0, HID routing, JPEG decode, per-bus writer threads.
 * The GD32 tty is opened O_RDONLY in phase 1; there is no write path at all. */
#include "gd32.h"
#include "host.h"
#include "screen.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static int open_gd32_ro(const char *dev)
{
    int fd = open(dev, O_RDONLY | O_NOCTTY | O_CLOEXEC);
    if (fd < 0) return -1;
    struct termios t;
    if (tcgetattr(fd, &t) == 0) {
        cfmakeraw(&t); cfsetispeed(&t, B115200); cfsetospeed(&t, B115200);
        t.c_cflag |= CLOCAL | CREAD; t.c_cflag &= ~(CSTOPB | PARENB | CRTSCTS);
        t.c_cc[VMIN] = 1; t.c_cc[VTIME] = 0;
        tcsetattr(fd, TCSANOW, &t);
    }
    return fd;
}

static int monitor(const char *dev)
{
    int fd = open_gd32_ro(dev);
    if (fd < 0) { perror(dev); return 1; }
    gd32_parser p; gd32_parser_init(&p);
    uint8_t buf[256];
    for (;;) {
        ssize_t n = read(fd, buf, sizeof buf);
        if (n <= 0) { perror("read"); return 1; }
        for (ssize_t i = 0; i < n; i++) {
            int len = gd32_feed(&p, buf[i]);
            if (len <= 0) continue;
            struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
            gd32_key_event ev;
            if (gd32_decode_key(p.buf, len, &ev) == 0)
                { printf("%ld.%06ld key col=%u row=%u pressed=%u keycode=0x%04x hex=", (long)ts.tv_sec, ts.tv_nsec / 1000,
                       ev.col, ev.row, ev.pressed, ev.keycode); for (int k = 0; k < len; k++) printf("%02x", p.buf[k]); printf("\n"); }
            else {
                printf("%ld.%06ld frame id=0x%02x len=%d hex=", (long)ts.tv_sec, ts.tv_nsec / 1000, p.buf[0], len); for (int k = 0; k < len; k++) printf("%02x", p.buf[k]); printf("\n");
            }
            fflush(stdout);
        }
    }
}

static int pattern(const char *fbdir)
{
    screen_t s;
    if (screen_init(&s, fbdir)) return 1;
    for (int i = 0; i < PANEL_COUNT; i++) {
        int w = PANELS[i].w, h = PANELS[i].h;
        uint16_t *px = malloc((size_t)w * h * 2);
        /* stripes: count of white bars == fb number, on a bus-coloured background */
        for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
            int bar = (x * (PANELS[i].fb * 2 + 1)) / w;
            px[y * w + x] = (bar & 1) ? 0xFFFF : (PANELS[i].bus ? 0x001F : 0xF800);
        }
        screen_set_tile565(&s, PANELS[i].fb, 0, 0, w, h, px);
        free(px);
    }
    int a = screen_flush_bus(&s, 0), b = screen_flush_bus(&s, 1);
    printf("pattern written bus0=%d bus1=%d errors=%u\n", a, b, s.write_errors);
    screen_free(&s);
    return (a < 0 || b < 0) ? 1 : 0;
}

int main(int argc, char **argv)
{
    if (argc >= 2 && !strcmp(argv[1], "--monitor")) return monitor(argc > 2 ? argv[2] : "/dev/ttyS1");
    if (argc >= 2 && !strcmp(argv[1], "--pattern")) return pattern(argc > 2 ? argv[2] : "/dev");
    if (argc >= 2 && !strcmp(argv[1], "--getinfo")) {
        char j[8192]; if (host_json_getinfo_ack(j, sizeof j, "mk20-kbd-0.1") < 0) return 1; puts(j); return 0;
    }
    fprintf(stderr, "usage: mk20-kbd --monitor [tty] | --pattern [fbdir] | --getinfo\n");
    return 2;
}
