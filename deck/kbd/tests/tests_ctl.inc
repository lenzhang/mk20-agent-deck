/* A1: HID scroll descriptor variant, pointer release, kbd.sock control (lease / e-stop). Included by test_kbd.c. */
#include "ctl.h"
#include <sys/un.h>

static int rd_reports_n(int fd, uint8_t out[][9], int max, int ptr_len)
{
    int n = 0; uint8_t b[9];
    while (n < max) {
        if (read(fd, b, 1) != 1) break;
        int len = b[0] == 1 ? 9 : b[0] == 2 ? ptr_len : 1;
        if (len > 1 && read(fd, b + 1, (size_t)len - 1) != len - 1) break;
        memset(out[n], 0, 9); memcpy(out[n], b, (size_t)len); n++;
    }
    return n;
}

static int nb_pipe(int p[2])
{
    if (pipe(p)) return -1;
    fcntl(p[0], F_SETFL, O_NONBLOCK); fcntl(p[1], F_SETFL, O_NONBLOCK);
    return 0;
}

static void test_hid_scroll(void)
{
    int p[2]; CHECK(nb_pipe(p) == 0);
    hid_t h; uint8_t rep[64][9];
    /* default: 6-byte pointer reports, scroll unsupported */
    hid_init(&h, "/nonexistent", NULL); hid_attach_fd(&h, p[1]); rd_reports_n(p[0], rep, 64, 6);
    CHECK(h.ptr_len == HID_PTR_LEN);
    CHECK(hid_scroll(&h, 1, 0) == -1);
    hid_pointer_abs(&h, 1, 300, 400);
    CHECK(rd_reports_n(p[0], rep, 64, 6) == 1 && rep[0][1] == 1);
    /* pointer release: buttons 0 at the last position, once */
    CHECK(hid_pointer_release(&h) == 1);
    int n = rd_reports_n(p[0], rep, 64, 6);
    CHECK(n == 1 && rep[0][0] == 2 && rep[0][1] == 0 && rep[0][2] == (300 & 0xFF) && rep[0][3] == (300 >> 8) && rep[0][4] == (400 & 0xFF));
    CHECK(hid_pointer_release(&h) == 0 && rd_reports_n(p[0], rep, 64, 6) == 0);
    /* scroll variant: 8-byte pointer reports (id, buttons, x16, y16, wheel s8, pan s8) */
    hid_init(&h, "/nonexistent", NULL); hid_set_scroll(&h, 1); hid_attach_fd(&h, p[1]); rd_reports_n(p[0], rep, 64, 8);
    CHECK(h.ptr_len == HID_PTR_LEN_SCROLL);
    hid_pointer_abs(&h, 0, 1000, 2000);
    CHECK(hid_scroll(&h, -3, 5) == 0);
    CHECK(hid_scroll(&h, 200, -200) == 0);   /* clamped to -127..127 */
    n = rd_reports_n(p[0], rep, 64, 8);
    CHECK(n == 3 && rep[0][6] == 0 && rep[0][7] == 0);
    CHECK(rep[1][0] == 2 && rep[1][2] == (1000 & 0xFF) && rep[1][3] == (1000 >> 8) && (int8_t)rep[1][6] == -3 && (int8_t)rep[1][7] == 5);
    CHECK((int8_t)rep[2][6] == 127 && (int8_t)rep[2][7] == -127);
    close(p[0]); close(p[1]);
}

static int H(ctl_t *c, int ci, long now, const char *line, char *rep)
{
    int n = ctl_handle_line(c, ci, line, strlen(line), now, rep, 512);
    if (n >= 0) rep[n] = 0;
    return n;
}
static int has(const char *s, const char *sub) { return strstr(s, sub) != NULL; }
static long tok_of(const char *rep) { long t = -1; kbd_json_int(rep, strlen(rep), "token", &t); return t; }

static void test_ctl_lease(void)
{
    int p[2]; CHECK(nb_pipe(p) == 0);
    hid_t h; hid_init(&h, "/nonexistent", NULL); hid_attach_fd(&h, p[1]);
    uint8_t rep[64][9]; rd_reports_n(p[0], rep, 64, 6);
    ctl_t c; ctl_init(&c, &h); char r[512], q[256];
    /* not allowed yet (power-on default: only the keyboard allows) */
    CHECK(H(&c, 0, 0, "{\"op\":\"hello\",\"id\":1}", r) > 0 && has(r, "\"ok\":true") && has(r, "\"state\":\"disabled\""));
    CHECK(H(&c, 0, 0, "{\"op\":\"lease.acquire\",\"ttl_ms\":1000}", r) > 0 && has(r, "\"err\":\"disabled\""));
    ctl_set_allowed(&c, 1);
    CHECK(H(&c, 0, 0, "{\"op\":\"lease.acquire\",\"ttl_ms\":50}", r) > 0 && has(r, "bad_ttl"));
    CHECK(H(&c, 0, 0, "{\"op\":\"lease.acquire\",\"owner\":\"agent\",\"ttl_ms\":1000,\"id\":7}", r) > 0 && has(r, "\"ok\":true") && has(r, "\"id\":7"));
    long t = tok_of(r); CHECK(t > 0);
    CHECK(H(&c, 1, 10, "{\"op\":\"lease.acquire\",\"ttl_ms\":1000}", r) > 0 && has(r, "busy"));
    /* acts: key down/up, modifier, pointer; wrong token / client / stale seq rejected */
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":1,\"kind\":\"key\",\"usage\":4,\"down\":1}", t);
    CHECK(H(&c, 0, 20, q, r) > 0 && has(r, "\"ok\":true"));
    int n = rd_reports_n(p[0], rep, 64, 6); CHECK(n == 1 && rep[0][3] == 4);
    CHECK(H(&c, 1, 20, q, r) > 0 && has(r, "no_lease"));        /* other client */
    CHECK(H(&c, 0, 20, q, r) > 0 && has(r, "bad_seq"));         /* replay */
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":2,\"kind\":\"key\",\"usage\":4,\"down\":1}", t + 1);
    CHECK(H(&c, 0, 20, q, r) > 0 && has(r, "no_lease"));
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":2,\"kind\":\"key\",\"usage\":227,\"down\":1}", t);   /* LGUI */
    CHECK(H(&c, 0, 20, q, r) > 0 && has(r, "\"ok\":true"));
    n = rd_reports_n(p[0], rep, 64, 6); CHECK(n == 1 && rep[0][1] == 0x08 && rep[0][3] == 4);
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":3,\"kind\":\"key\",\"usage\":200,\"down\":1}", t);
    CHECK(H(&c, 0, 20, q, r) > 0 && has(r, "bad_usage"));
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":4,\"kind\":\"ptr\",\"buttons\":1,\"x\":100,\"y\":200}", t);
    CHECK(H(&c, 0, 20, q, r) > 0 && has(r, "\"ok\":true"));
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":5,\"kind\":\"ptr\",\"buttons\":1,\"x\":40000,\"y\":200}", t);
    CHECK(H(&c, 0, 20, q, r) > 0 && has(r, "out_of_range"));
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":6,\"kind\":\"scroll\",\"wheel\":-2}", t);
    CHECK(H(&c, 0, 20, q, r) > 0 && has(r, "unsupported"));   /* default descriptor has no wheel */
    n = rd_reports_n(p[0], rep, 64, 6); CHECK(n == 1 && rep[0][0] == 2 && rep[0][1] == 1);
    /* renew extends; expiry revokes with all-keys-up + pointer buttons up */
    snprintf(q, sizeof q, "{\"op\":\"lease.renew\",\"token\":%ld,\"ttl_ms\":1000}", t);
    CHECK(H(&c, 0, 900, q, r) > 0 && has(r, "\"ok\":true"));
    ctl_tick(&c, 1800); CHECK(c.token != 0);
    CHECK(ctl_timeout_ms(&c, 1800, 250) == 100 && ctl_timeout_ms(&c, 1000, 250) == 250);
    ctl_tick(&c, 1900); CHECK(c.token == 0 && c.st.expiries == 1);
    n = rd_reports_n(p[0], rep, 64, 6);
    CHECK(n == 2 && rep[0][0] == 1 && rep[0][1] == 0 && rep[0][3] == 0 && rep[1][0] == 2 && rep[1][1] == 0 && rep[1][2] == 100);
    CHECK(H(&c, 0, 1900, q, r) > 0 && has(r, "no_lease"));
    /* human key press preempts (no latch: the lease can be re-acquired) */
    CHECK(H(&c, 0, 2000, "{\"op\":\"lease.acquire\",\"ttl_ms\":3000}", r) > 0); t = tok_of(r); CHECK(t > 0);
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":1,\"kind\":\"key\",\"usage\":5,\"down\":1}", t);
    CHECK(H(&c, 0, 2000, q, r) > 0); rd_reports_n(p[0], rep, 64, 6);
    ctl_human_input(&c, 2010);
    CHECK(c.token == 0 && c.st.preempts == 1 && !c.estopped);
    n = rd_reports_n(p[0], rep, 64, 6); CHECK(n == 1 && rep[0][3] == 0);
    /* release */
    CHECK(H(&c, 0, 2100, "{\"op\":\"lease.acquire\",\"ttl_ms\":3000}", r) > 0); t = tok_of(r);
    snprintf(q, sizeof q, "{\"op\":\"lease.release\",\"token\":%ld}", t);
    CHECK(H(&c, 0, 2100, q, r) > 0 && has(r, "\"ok\":true") && c.token == 0);
    /* e-stop: latch, everything up, socket cannot re-allow; keyboard can */
    CHECK(H(&c, 0, 2200, "{\"op\":\"lease.acquire\",\"ttl_ms\":3000}", r) > 0); t = tok_of(r);
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":1,\"kind\":\"ptr\",\"buttons\":2,\"x\":5,\"y\":6}", t);
    CHECK(H(&c, 0, 2200, q, r) > 0); rd_reports_n(p[0], rep, 64, 6);
    CHECK(H(&c, 1, 2210, "{\"op\":\"estop\"}", r) > 0 && has(r, "stopped"));
    CHECK(c.estopped && !c.allowed && c.token == 0 && c.st.estops == 1);
    n = rd_reports_n(p[0], rep, 64, 6); CHECK(n == 1 && rep[0][0] == 2 && rep[0][1] == 0);   /* keys were already up: only ptr */
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":2,\"kind\":\"key\",\"usage\":5,\"down\":1}", t);
    CHECK(H(&c, 0, 2220, q, r) > 0 && has(r, "stopped"));
    CHECK(H(&c, 0, 2220, "{\"op\":\"lease.acquire\",\"ttl_ms\":3000}", r) > 0 && has(r, "stopped"));
    CHECK(H(&c, 0, 2220, "{\"op\":\"allow\"}", r) > 0 && has(r, "unknown_op"));
    CHECK(H(&c, 0, 2220, "{\"op\":\"status\"}", r) > 0 && has(r, "\"state\":\"stopped\""));
    ctl_set_allowed(&c, 1);
    CHECK(H(&c, 0, 2300, "{\"op\":\"lease.acquire\",\"ttl_ms\":3000}", r) > 0 && has(r, "\"ok\":true"));
    /* garbage */
    CHECK(H(&c, 0, 2300, "not json", r) > 0 && has(r, "bad_request"));
    CHECK(H(&c, 0, 2300, "{\"op\":\"act\"}", r) > 0 && has(r, "bad_token"));
    close(p[0]); close(p[1]);
}

static void test_ctl_scroll_act(void)
{
    int p[2]; CHECK(nb_pipe(p) == 0);
    hid_t h; hid_init(&h, "/nonexistent", NULL); hid_set_scroll(&h, 1); hid_attach_fd(&h, p[1]);
    uint8_t rep[64][9]; rd_reports_n(p[0], rep, 64, 8);
    ctl_t c; ctl_init(&c, &h); ctl_set_allowed(&c, 1); char r[512], q[256];
    CHECK(H(&c, 0, 0, "{\"op\":\"lease.acquire\",\"ttl_ms\":3000}", r) > 0); long t = tok_of(r);
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":1,\"kind\":\"scroll\",\"wheel\":-2,\"pan\":3}", t);
    CHECK(H(&c, 0, 0, q, r) > 0 && has(r, "\"ok\":true"));
    int n = rd_reports_n(p[0], rep, 64, 8);
    CHECK(n == 1 && (int8_t)rep[0][6] == -2 && (int8_t)rep[0][7] == 3);
    snprintf(q, sizeof q, "{\"op\":\"act\",\"token\":%ld,\"seq\":2,\"kind\":\"scroll\",\"wheel\":300}", t);
    CHECK(H(&c, 0, 0, q, r) > 0 && has(r, "out_of_range"));
    CHECK(H(&c, 0, 0, "{\"op\":\"status\"}", r) > 0 && has(r, "\"scroll\":1"));
    close(p[0]); close(p[1]);
}

static void test_ctl_socket(void)
{
    char dir[] = "/tmp/mk20ctlXXXXXX"; CHECK(mkdtemp(dir) != NULL);
    char path[64]; snprintf(path, sizeof path, "%s/kbd.sock", dir);
    ctl_t c; ctl_init(&c, NULL);
    CHECK(ctl_listen(&c, path) == 0);
    struct stat st; CHECK(stat(path, &st) == 0 && (st.st_mode & 0777) == 0600);
    int fds[3];
    for (int i = 0; i < 3; i++) {
        fds[i] = socket(AF_UNIX, SOCK_STREAM, 0);
        struct sockaddr_un a; memset(&a, 0, sizeof a); a.sun_family = AF_UNIX; snprintf(a.sun_path, sizeof a.sun_path, "%s", path);
        CHECK(connect(fds[i], (struct sockaddr *)&a, sizeof a) == 0);
        struct pollfd pf[4]; int n = ctl_pollfds(&c, pf, 4); poll(pf, (nfds_t)n, 200); ctl_on_poll(&c, pf, n, 0);
    }
    CHECK(c.cfd[0] >= 0 && c.cfd[1] >= 0);
    char b[256]; ssize_t r = read(fds[2], b, sizeof b - 1); b[r > 0 ? r : 0] = 0;
    CHECK(has(b, "too_many_clients"));
    /* request split across writes + two requests in one write */
    CHECK(write(fds[0], "{\"op\":\"hel", 10) == 10);
    struct pollfd pf[4]; int n = ctl_pollfds(&c, pf, 4); poll(pf, (nfds_t)n, 200); ctl_on_poll(&c, pf, n, 0);
    CHECK(write(fds[0], "lo\",\"id\":3}\n{\"op\":\"estop\",\"id\":4}\n", 34) == 34);
    n = ctl_pollfds(&c, pf, 4); poll(pf, (nfds_t)n, 200); ctl_on_poll(&c, pf, n, 0);
    usleep(20000);
    r = read(fds[0], b, sizeof b - 1); b[r > 0 ? r : 0] = 0;
    CHECK(has(b, "\"id\":3") && has(b, "\"ev\":\"estop\"") && has(b, "\"id\":4"));
    r = read(fds[1], b, sizeof b - 1); b[r > 0 ? r : 0] = 0;
    CHECK(has(b, "\"ev\":\"estop\""));   /* broadcast */
    /* overlong line -> client dropped */
    char big[CTL_LINE_MAX + 10]; memset(big, 'a', sizeof big);
    CHECK(write(fds[1], big, sizeof big) == (ssize_t)sizeof big);
    for (int k = 0; k < 4 && c.cfd[1] >= 0; k++) { n = ctl_pollfds(&c, pf, 4); poll(pf, (nfds_t)n, 200); ctl_on_poll(&c, pf, n, 0); }
    CHECK(c.cfd[1] < 0);
    for (int i = 0; i < 3; i++) close(fds[i]);
    ctl_close(&c);
    CHECK(access(path, F_OK) != 0);
    rmdir(dir);
}
