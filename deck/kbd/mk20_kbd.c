/* mk20-kbd: KD replacement daemon (option D).
 *   --run       daemon: GD32 knob init (allow-listed 0x05 slots 100..105 only), host link
 *               (A1A55A5E subset: getInfo, keyStateChanged, cmd100 JPEG, setBacklight, cmd200 tile),
 *               direct dirty-panel fb writes. --no-host = local mode with on-panel key feedback.
 *   --monitor   read /dev/ttyS1 RECEIVE-ONLY (O_RDONLY), log frames
 *   --pattern   write an id test pattern to <fbdir>/fb1..fb21 (needs KD stopped)
 *   --getinfo   print the getInfo JSON we answer with
 * The ONLY GD32 write path is gd32_tx() (gd32io.c) behind the strict allow-list. */
#include "gd32.h"
#include "gd32io.h"
#include "host.h"
#include "kbd.h"
#include "screen.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#define KBD_VERSION "mk20-kbd-0.3"

static int open_gd32_ro(const char *dev) { return gd32_open(dev, 0); }

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

static volatile sig_atomic_t g_stop;
static void on_sig(int s) { (void)s; g_stop = 1; }

static long mono_ms(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec * 1000L + t.tv_nsec / 1000000L; }

static int open_host(const char *path)
{
    int fd = open(path, O_RDWR | O_NOCTTY | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) return -1;
    struct termios t;
    if (tcgetattr(fd, &t) == 0) { cfmakeraw(&t); tcsetattr(fd, TCSANOW, &t); }
    int fl = fcntl(fd, F_GETFL); fcntl(fd, F_SETFL, fl & ~O_NONBLOCK);   /* blocking writes */
    return fd;
}

struct run_opts { const char *gd32, *host, *fb, *bl, *status; int no_host, no_write, timeout_ms; };

static int run(const struct run_opts *o)
{
    signal(SIGPIPE, SIG_IGN);
    struct sigaction sa; memset(&sa, 0, sizeof sa); sa.sa_handler = on_sig;
    sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL);
    kbd_t k;
    if (kbd_init(&k, o->fb, o->bl, KBD_VERSION)) { fprintf(stderr, "mk20-kbd: init failed\n"); return 1; }
    k.local_ui = o->no_host;
    kbd_draw_idle(&k);
    if (kbd_flush(&k) < 0) fprintf(stderr, "mk20-kbd: initial panel write failed (errors=%u)\n", k.scr.write_errors);

    /* kill file: the fixed path, or an extra one from MK20_KBD_NO_WRITE_FILE (tests, TF rescue);
     * either existing keeps the GD32 tty read-only. The env can only ADD a kill file. */
    const char *extra = getenv("MK20_KBD_NO_WRITE_FILE");
    int no_write = o->no_write || access(GD32_NO_WRITE_FILE, F_OK) == 0 || (extra && *extra && access(extra, F_OK) == 0);
    int gfd = gd32_open(o->gd32, !no_write);
    if (gfd < 0) { perror(o->gd32); kbd_free(&k); return 1; }
    k.gd32_rw = !no_write;
    gd32_parser gp; gd32_parser_init(&gp);
    /* host link first, so key events arriving during knob init (<= 18 s worst case if the
     * GD32 never echoes) are still forwarded */
    if (!o->no_host && (k.host_fd = open_host(o->host)) >= 0) fprintf(stderr, "mk20-kbd: host link %s open\n", o->host);
    long next_host_try = mono_ms() + 1000;
    if (no_write) {
        k.knob.state = 4;
        fprintf(stderr, "mk20-kbd: GD32 write disabled (%s); knob K2 stays as configured in the GD32\n",
                o->no_write ? "--no-gd32-write" : "kill file");
    } else {
        gd32_knob_opts ko = {o->timeout_ms, 3};
        gd32_knob_init(gfd, &gp, &ko, &k.knob, kbd_gd32_cb, &k);
        fprintf(stderr, "mk20-kbd: knob init %s (%d/6 slots, timeouts=%d tx_errors=%d)\n",
                gd32_knob_state_name(k.knob.state), k.knob.ok_count, k.knob.timeouts, k.knob.tx_errors);
    }
    if (o->status) kbd_write_status(&k, o->status);

    long next_status = mono_ms() + 1000;
    uint8_t buf[16384];
    while (!g_stop) {
        long now = mono_ms();
        if (!o->no_host && k.host_fd < 0 && now >= next_host_try) {
            k.host_fd = open_host(o->host);
            next_host_try = now + 1000;
            if (k.host_fd >= 0) fprintf(stderr, "mk20-kbd: host link %s open\n", o->host);
        }
        struct pollfd pf[2] = {{gfd, POLLIN, 0}, {k.host_fd, POLLIN, 0}};
        int np = k.host_fd >= 0 ? 2 : 1;
        int r = poll(pf, (nfds_t)np, 250);
        if (r < 0 && errno != EINTR) { perror("poll"); break; }
        if (r > 0 && (pf[0].revents & (POLLIN | POLLERR | POLLHUP))) {
            ssize_t n = read(gfd, buf, sizeof buf);
            if (n <= 0 && errno != EINTR) { perror("gd32 read"); break; }
            for (ssize_t i = 0; i < n; i++) { int l = gd32_feed(&gp, buf[i]); if (l > 0) kbd_on_gd32(&k, gp.buf, l); }
        }
        if (r > 0 && np == 2 && (pf[1].revents & (POLLIN | POLLERR | POLLHUP))) {
            ssize_t n = read(k.host_fd, buf, sizeof buf);
            if (n > 0) kbd_host_feed(&k, buf, (size_t)n);
            else if (n == 0 || (errno != EINTR && errno != EAGAIN)) {   /* pty master gone (bridge restart): EIO */
                close(k.host_fd); k.host_fd = -1; next_host_try = mono_ms() + 500;
                fprintf(stderr, "mk20-kbd: host link closed, retrying\n");
            }
        }
        if (o->status && mono_ms() >= next_status) { kbd_write_status(&k, o->status); next_status = mono_ms() + 1000; }
    }
    if (o->status) kbd_write_status(&k, o->status);
    if (k.host_fd >= 0) close(k.host_fd);
    close(gfd);
    kbd_free(&k);
    return 0;
}

static void usage(void)
{
    fprintf(stderr, "usage: mk20-kbd --run [--no-host] [--no-gd32-write] [--gd32 TTY] [--host TTY] [--fb DIR]\n"
                    "                      [--bl DIR|''] [--status FILE] [--knob-timeout-ms N]\n"
                    "       mk20-kbd --monitor [tty] | --pattern [fbdir] | --getinfo\n"
                    "  kill file " GD32_NO_WRITE_FILE " = same as --no-gd32-write\n");
}

int main(int argc, char **argv)
{
    if (argc >= 2 && !strcmp(argv[1], "--monitor")) return monitor(argc > 2 ? argv[2] : "/dev/ttyS1");
    if (argc >= 2 && !strcmp(argv[1], "--pattern")) return pattern(argc > 2 ? argv[2] : "/dev");
    if (argc >= 2 && !strcmp(argv[1], "--getinfo")) {
        char j[8192]; if (host_json_getinfo_ack(j, sizeof j, KBD_VERSION) < 0) return 1; puts(j); return 0;
    }
    if (argc >= 2 && !strcmp(argv[1], "--run")) {
        struct run_opts o = {"/dev/ttyS1", "/dev/ttyGS0", "/dev", "/sys/kernel/debug/dispdbg", "/run/mk20/kbd.status", 0, 0, 1000};
        for (int i = 2; i < argc; i++) {
            const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
            if (!strcmp(a, "--no-host")) o.no_host = 1;
            else if (!strcmp(a, "--no-gd32-write")) o.no_write = 1;
            else if (v && !strcmp(a, "--gd32")) { o.gd32 = v; i++; }
            else if (v && !strcmp(a, "--host")) { o.host = v; i++; }
            else if (v && !strcmp(a, "--fb")) { o.fb = v; i++; }
            else if (v && !strcmp(a, "--bl")) { o.bl = v; i++; }
            else if (v && !strcmp(a, "--status")) { o.status = *v ? v : NULL; i++; }
            else if (v && !strcmp(a, "--knob-timeout-ms")) { o.timeout_ms = atoi(v); i++; }
            else { usage(); return 2; }
        }
        return run(&o);
    }
    usage();
    return 2;
}
