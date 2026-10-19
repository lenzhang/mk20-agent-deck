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
    /* Desired buttons are already zero but queued moves/scroll still require one neutral report. */
    h.fd = -1; hid_pointer_abs(&h, 0, 123, 456); hid_scroll(&h, 1, 2);
    CHECK(h.ptr_n == 2 && hid_pointer_release(&h) == 1 && h.ptr_n == 1);
    h.fd = p[1]; hid_service(&h, 0);
    CHECK(rd_reports_n(p[0], rep, 64, 8) == 1 && !rep[0][1] && !rep[0][6] && !rep[0][7]);
    close(p[0]); close(p[1]);
}

static int H(ctl_t *c, int ci, int64_t now, const char *line, char *rep)
{
    int n = ctl_handle_line(c, ci, line, strlen(line), now, rep, CTL_LINE_MAX);
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
    ctl_t c; ctl_init(&c, &h); char r[4096], q[256];
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
    ctl_t c; ctl_init(&c, &h); ctl_set_allowed(&c, 1); char r[4096], q[256];
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
    char b[4096]; ssize_t r = read(fds[2], b, sizeof b - 1); b[r > 0 ? r : 0] = 0;
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

static const char *confirm_req = "{\"op\":\"confirm.request\",\"id\":7,\"cid\":42,\"summary\":\"Send it?\",\"risk\":\"high\"}";
static void event_has(int fd, const char *part)
{
    char b[8192]; ssize_t n = recv(fd, b, sizeof b - 1, MSG_DONTWAIT);
    b[n > 0 ? n : 0] = 0; CHECK(n > 0 && has(b, part));
}
static void test_ctl_confirm(void)
{
    ctl_t c; ctl_init(&c, NULL); char r[4096]; int p[2];
    CHECK(H(&c, 0, 0, confirm_req, r) > 0 && has(r, "disabled"));
    ctl_set_allowed(&c, 1);
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, p) == 0); c.cfd[0] = p[0];
    CHECK(H(&c, 0, 100, confirm_req, r) > 0 && has(r, "\"ok\":true") && !c.token);
    CHECK(c.confirm.deadline_ms == 30100 && ctl_timeout_ms(&c, 30000, 250) == 100);
    CHECK(ctl_timeout_ms(&c, 30100, 250) == 0);
    CHECK(H(&c, 1, 101, confirm_req, r) > 0 && has(r, "busy"));
    CHECK(H(&c, 0, 102, "{\"op\":\"status\"}", r) > 0 && has(r, "\"pending\":true"));
    ctl_confirm_key(&c, 1, 110); CHECK(c.confirm.pending && c.st.confirm_ignored == 1);
    ctl_confirm_shown(&c, 200); ctl_confirm_shown(&c, 250); /* redraw never resets guard */
    ctl_confirm_key(&c, 1, 499); CHECK(c.confirm.pending && c.st.confirm_ignored == 2);
    ctl_confirm_key(&c, 1, 500); CHECK(!c.confirm.pending); event_has(p[1], "\"cid\":42,\"ok\":true,\"reason\":\"confirmed\"");
    CHECK(H(&c, 0, 600, confirm_req, r) > 0); ctl_confirm_key(&c, 0, 600);
    CHECK(!c.confirm.pending); event_has(p[1], "\"reason\":\"cancel\"");
    CHECK(H(&c, 0, 700, confirm_req, r) > 0); ctl_tick(&c, 30699); CHECK(c.confirm.pending);
    ctl_tick(&c, 30700); CHECK(!c.confirm.pending); event_has(p[1], "\"reason\":\"timeout\"");
    CHECK(H(&c, 0, 40000, confirm_req, r) > 0); ctl_set_allowed(&c, 0);
    CHECK(!c.confirm.pending); event_has(p[1], "\"reason\":\"disabled\"");
    ctl_set_allowed(&c, 1); CHECK(H(&c, 0, 41000, confirm_req, r) > 0); ctl_estop(&c, "test", 41001);
    CHECK(!c.confirm.pending); event_has(p[1], "\"reason\":\"estop\"");
    CHECK(H(&c, 0, 42000, confirm_req, r) > 0 && has(r, "stopped"));
    ctl_set_allowed(&c, 1); CHECK(H(&c, 0, 43000, confirm_req, r) > 0);
    ctl_client_gone(&c, 1, 43001); CHECK(c.confirm.pending);
    ctl_client_gone(&c, 0, 43002); CHECK(!c.confirm.pending); close(p[1]);
    const char *bad[] = {
        "{\"op\":\"confirm.request\",\"cid\":1,\"summary\":\"x\",\"risk\":\"medium\"}",
        "{\"op\":\"confirm.request\",\"cid\":1,\"summary\":\"x\",\"risk\":\"low\",\"timeout_ms\":999}",
        "{\"op\":\"confirm.request\",\"cid\":1,\"summary\":\"x\",\"risk\":\"low\",\"timeout_ms\":120001}",
        "{\"op\":\"confirm.request\",\"cid\":1,\"summary\":\"x\",\"risk\":\"low\",\"timeout_ms\":\"bad\"}",
        "{\"op\":\"confirm.request\",\"summary\":\"x\",\"risk\":\"low\"}"
    };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) CHECK(H(&c, 0, 0, bad[i], r) > 0 && has(r, "bad_args"));
    char q[768], summary[122]; memset(summary, 'x', 121); summary[121] = 0;
    snprintf(q, sizeof q, "{\"op\":\"confirm.request\",\"cid\":1,\"summary\":\"%s\",\"risk\":\"low\"}", summary);
    CHECK(H(&c, 0, 0, q, r) > 0 && has(r, "bad_args"));
    summary[120] = 0;
    for (int i = 0; i < 2; i++) {
        snprintf(q, sizeof q, "{\"op\":\"confirm.request\",\"cid\":1,\"summary\":\"%s\",\"risk\":\"low\",\"timeout_ms\":%d}", summary, i ? 120000 : 1000);
        CHECK(H(&c, 0, 0, q, r) > 0 && has(r, "\"ok\":true")); ctl_confirm_key(&c, 0, 0);
    }
    CHECK(H(&c, 0, 0, "{\"op\":\"confirm.request\",\"cid\":3,\"summary\":\"\\u4e2d\\u6587 \\\"ok\\\"\",\"risk\":\"low\"}", r) > 0 && c.confirm.pending);
    CHECK(!strcmp(c.confirm.summary, "中文 \"ok\"")); ctl_confirm_key(&c, 0, 0);
    CHECK(H(&c, 0, 0, "{\"op\":\"status\",\"op\":\"estop\"}", r) > 0 && has(r, "bad_request"));
    CHECK(H(&c, 0, 0, "{\"op\":\"status\"}garbage", r) > 0 && has(r, "bad_request"));
}

static void test_ctl_ui(void)
{
    ctl_t c; ctl_init(&c, NULL); char r[4096], q[1024];
    const char *good = "{\"op\":\"ui.agent\",\"state\":\"acting\",\"input\":\"vnc\",\"summary\":\"go\",\"progress\":100}";
    CHECK(!ctl_ui_dirty(&c));
    CHECK(H(&c, 0, 0, good, r) > 0 && has(r, "disabled")); ctl_set_allowed(&c, 1);
    CHECK(H(&c, 0, 0, good, r) > 0 && has(r, "\"ok\":true") && !c.token);
    CHECK(ctl_ui_dirty(&c) && !ctl_ui_dirty(&c));
    CHECK(H(&c, 0, 0, good, r) > 0 && !ctl_ui_dirty(&c));
    CHECK(H(&c, 0, 0, "{\"op\":\"status\"}", r) > 0 && has(r, "\"input\":\"vnc\"") && has(r, "\"progress\":100"));
    const char *states[] = {"idle", "observing", "planning", "acting", "awaiting_confirm", "done", "stopped", "error", "bad"};
    for (int i = 0; i < 9; i++) {
        snprintf(q, sizeof q, "{\"op\":\"ui.agent\",\"state\":\"%s\",\"input\":\"hid\",\"summary\":\"\\u4e2d\\u6587\",\"progress\":0}", states[i]);
        CHECK(H(&c, 0, 0, q, r) > 0 && has(r, i == 8 ? "bad_args" : "\"ok\":true"));
    }
    const char *bad[] = {
        "{\"op\":\"ui.agent\",\"state\":\"idle\",\"input\":\"bad\",\"summary\":\"x\",\"progress\":0}",
        "{\"op\":\"ui.agent\",\"state\":\"idle\",\"input\":\"hid\",\"summary\":\"x\",\"progress\":-1}",
        "{\"op\":\"ui.agent\",\"state\":\"idle\",\"input\":\"hid\",\"summary\":\"x\",\"progress\":101}",
        "{\"op\":\"ui.agent\",\"state\":\"idle\",\"input\":\"hid\",\"summary\":\"x\",\"progress\":\"0\"}",
        "{\"op\":\"ui.agent\",\"state\":\"idle\",\"input\":\"hid\",\"progress\":0}"
    };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) CHECK(H(&c, 0, 0, bad[i], r) > 0 && has(r, "bad_args"));
    char summary[122]; memset(summary, 'x', 121); summary[121] = 0;
    snprintf(q, sizeof q, "{\"op\":\"ui.agent\",\"state\":\"idle\",\"input\":\"hid\",\"summary\":\"%s\",\"progress\":0}", summary);
    CHECK(H(&c, 0, 0, q, r) > 0 && has(r, "bad_args"));
    ctl_estop(&c, "test", 0); CHECK(H(&c, 0, 0, good, r) > 0 && has(r, "stopped"));
}

static void screen_req(ctl_t *c, int ci, int64_t now, const char *q, const char *part)
{
    char r[4096]; int n = H(c, ci, now, q, r);
    if (n <= 0 || !has(r, part)) fprintf(stderr, "request %s -> %s (want %s)\n", q, r, part);
    CHECK(n > 0 && has(r, part));
}
static void screen_text(ctl_t *c, int64_t now, int fb, const char *part)
{
    char q[256]; snprintf(q, sizeof q, "{\"op\":\"screen.text\",\"page_id\":3,\"fb\":%d,\"text\":\"x\",\"size\":16,\"fg\":65535,\"bg\":0}", fb);
    screen_req(c, 0, now, q, part);
}
static void test_ctl_screen(void)
{
    ctl_t c; ctl_init(&c, NULL); char r[4096], q[1024];
    screen_req(&c, 0, 0, "{\"op\":\"screen.layout\"}", "disabled");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.create\",\"name\":\"x\"}", "disabled");
    ctl_set_allowed(&c, 1);
    CHECK(H(&c, 0, 0, "{\"op\":\"screen.layout\"}", r) > 0);
    CHECK(has(r, "[9,128,128,0,1,3]") && has(r, "[10,128,128,1,1,4]"));
    CHECK(has(r, "[20,128,128,1,3,4]") && has(r, "[21,428,142,0,null,null]"));
    CHECK(has(r, "[0,\"system\"]") && has(r, "[2,\"user\"]"));
    for (int i = 0; i < 8; i++) screen_req(&c, i == 1 ? 1 : 0, 0, "{\"op\":\"screen.page.create\",\"name\":\"work\"}", "page_id");
    CHECK(H(&c, 0, 0, "{\"op\":\"screen.layout\"}", r) > 0 && strlen(r) < CTL_LINE_MAX);
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.create\",\"name\":\"ninth\"}", "busy");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.delete\",\"page_id\":1}", "reserved");
    screen_req(&c, 0, 0, "{\"op\":\"screen.draw\",\"page_id\":1}", "reserved");
    screen_req(&c, 0, 0, "{\"op\":\"screen.bind\",\"page_id\":1}", "reserved");
    screen_req(&c, 0, 0, "{\"op\":\"screen.bind\",\"page_id\":3,\"row\":3,\"col\":4,\"action\":\"x\"}", "reserved");
    screen_text(&c, 0, 20, "reserved");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.show\",\"page_id\":0}", "forbidden");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.show\",\"page_id\":4}", "forbidden");
    screen_req(&c, 0, 0, "{\"op\":\"screen.text\",\"page_id\":0,\"fb\":1,\"text\":\"x\",\"size\":1,\"fg\":0,\"bg\":0}", "forbidden");
    ctl_screen_grant(&c, 1000, 100);
    screen_req(&c, 0, 1099, "{\"op\":\"screen.text\",\"page_id\":0,\"fb\":1,\"text\":\"x\",\"size\":1,\"fg\":0,\"bg\":0}", "rendered\":false");
    screen_req(&c, 0, 1100, "{\"op\":\"screen.text\",\"page_id\":2,\"fb\":1,\"text\":\"x\",\"size\":1,\"fg\":0,\"bg\":0}", "forbidden");
    CHECK(!c.screen.grant_until); /* request processing ticks expiration */
    const char *bad[] = {
        "\"x\":-1,\"y\":0,\"w\":1,\"h\":1,\"fmt\":\"rgb565\",\"bytes\":2",
        "\"x\":128,\"y\":0,\"w\":1,\"h\":1,\"fmt\":\"rgb565\",\"bytes\":2",
        "\"x\":0,\"y\":128,\"w\":1,\"h\":1,\"fmt\":\"rgb565\",\"bytes\":2",
        "\"x\":0,\"y\":0,\"w\":0,\"h\":1,\"fmt\":\"rgb565\",\"bytes\":2",
        "\"x\":0,\"y\":0,\"w\":129,\"h\":1,\"fmt\":\"rgb565\",\"bytes\":258",
        "\"x\":0,\"y\":0,\"w\":1,\"h\":1,\"fmt\":\"png\",\"bytes\":2",
        "\"x\":0,\"y\":0,\"w\":1,\"h\":1,\"fmt\":\"jpeg\",\"bytes\":65537",
        "\"x\":0,\"y\":0,\"w\":1,\"h\":1,\"fmt\":\"jpeg\",\"bytes\":0",
        "\"x\":0,\"y\":0,\"w\":1,\"h\":1,\"fmt\":\"rgb565\",\"bytes\":1"
    };
    for (size_t i = 0; i < sizeof bad / sizeof *bad; i++) {
        snprintf(q, sizeof q, "{\"op\":\"screen.draw\",\"page_id\":3,\"fb\":1,%s}", bad[i]); screen_req(&c, 0, 2000, q, "bad_args");
    }
    screen_req(&c, 0, 2000, "{\"op\":\"screen.draw\",\"page_id\":3,\"fb\":21,\"x\":427,\"y\":141,\"w\":1,\"h\":1,\"fmt\":\"jpeg\",\"bytes\":65536}", "rendered\":false");
    screen_req(&c, 0, 2000, "{\"op\":\"screen.text\",\"page_id\":3,\"fb\":1,\"text\":\"x\",\"size\":129,\"fg\":0,\"bg\":0}", "bad_args");
    screen_req(&c, 0, 2000, "{\"op\":\"screen.text\",\"page_id\":3,\"fb\":1,\"text\":\"x\",\"size\":1,\"fg\":65536,\"bg\":0}", "bad_args");
    for (int i = 0; i < 10; i++) screen_text(&c, 3000 + i, 1, "rendered\":false");
    screen_text(&c, 3999, 1, "rate_limited"); screen_text(&c, 4000, 1, "rendered\":false");
    screen_text(&c, 4000, 1, "rate_limited"); screen_text(&c, 4001, 1, "rendered\":false");
    for (int fb = 1; fb <= 3; fb++) for (int i = 0; i < 10; i++) screen_text(&c, 5100, fb, "rendered\":false");
    screen_text(&c, 6099, 4, "rate_limited"); screen_text(&c, 6100, 4, "rendered\":false");
    int a[2], b[2]; CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, a) == 0 && socketpair(AF_UNIX, SOCK_STREAM, 0, b) == 0);
    c.cfd[0] = a[0]; c.cfd[1] = b[0];
    screen_req(&c, 0, 7000, "{\"op\":\"screen.bind\",\"page_id\":3,\"row\":0,\"col\":0,\"action\":\"click\"}", "\"ok\":true");
    screen_req(&c, 1, 7000, "{\"op\":\"screen.bind\",\"page_id\":4,\"row\":0,\"col\":0,\"action\":\"other\"}", "\"ok\":true");
    screen_req(&c, 0, 7000, "{\"op\":\"screen.page.show\",\"page_id\":3}", "\"ok\":true");
    ctl_key_event(&c, 0, 0, 1, 7000); event_has(a[1], "\"page\":3");
    CHECK(recv(b[1], r, sizeof r, MSG_DONTWAIT) < 0);
    ctl_key_event(&c, 0, 1, 1, 7000); CHECK(recv(a[1], r, sizeof r, MSG_DONTWAIT) < 0);
    screen_req(&c, 1, 7000, "{\"op\":\"screen.page.show\",\"page_id\":4}", "\"ok\":true");
    ctl_key_event(&c, 0, 0, 0, 7000); event_has(b[1], "\"page\":4,\"row\":0,\"col\":0,\"down\":false");
    CHECK(recv(a[1], r, sizeof r, MSG_DONTWAIT) < 0);
    screen_req(&c, 0, 7000, "{\"op\":\"screen.page.show\",\"page_id\":1}", "\"ok\":true");
    ctl_key_event(&c, 0, 0, 1, 7000); CHECK(recv(a[1], r, sizeof r, MSG_DONTWAIT) < 0);
    ctl_screen_grant(&c, 1000, 7000); ctl_set_allowed(&c, 0);
    CHECK(c.screen.current == 0 && c.screen.page[3].used && !c.screen.page[3].bound && !c.screen.grant_until);
    ctl_set_allowed(&c, 1); ctl_screen_grant(&c, 1000, 7000); ctl_estop(&c, "test", 7000);
    CHECK(c.screen.current == 0 && !c.screen.grant_until && c.screen.page[4].used && !c.screen.page[4].bound);
    screen_req(&c, 0, 7000, "{\"op\":\"screen.layout\"}", "stopped");
    ctl_set_allowed(&c, 1);
    screen_req(&c, 0, 7000, "{\"op\":\"screen.page.delete\",\"page_id\":3}", "\"ok\":true");
    screen_req(&c, 0, 7000, "{\"op\":\"screen.page.show\",\"page_id\":3}", "not_found");
    ctl_client_gone(&c, 0, 7001); ctl_client_gone(&c, 1, 7001); close(a[1]); close(b[1]);
    CHECK(!c.screen.page[4].used);
    ctl_init(&c, NULL); CHECK(c.screen.grant_until == 0 && !c.screen.page[3].used);
}

static void test_k2_estop(void)
{
    k2_estop_t k; k2_estop_init(&k, 1);
    CHECK(!k2_estop_event(&k, 103, 1, 100));
    CHECK(!k2_estop_tick(&k, 1099) && k2_estop_timeout(&k, 1099, 250) == 1);
    CHECK(!k2_estop_event(&k, 103, 1, 500)); /* duplicate down never restarts timer */
    CHECK(!strcmp(k2_estop_tick(&k, 1100), "k2_hold"));
    CHECK(!k2_estop_tick(&k, 1200) && k2_estop_timeout(&k, 1200, 250) == 250);
    CHECK(!k2_estop_event(&k, 103, 0, 1300));
    CHECK(!k2_estop_event(&k, 103, 1, 1400) && !k2_estop_event(&k, 103, 0, 2399));
    CHECK(!k2_estop_tick(&k, 5000));
    k2_estop_init(&k, 0); CHECK(!k2_estop_event(&k, 103, 1, 0));
    CHECK(!k2_estop_tick(&k, 5000) && k2_estop_timeout(&k, 5000, 250) == 250);
    CHECK(!k2_estop_event(&k, 103, 1, 1000));
    CHECK(!strcmp(k2_estop_event(&k, 103, 1, 1500), "k2_triple"));
    k2_estop_init(&k, 0); CHECK(!k2_estop_event(&k, 103, 1, 0));
    CHECK(!k2_estop_event(&k, 103, 1, 1000) && !k2_estop_event(&k, 103, 1, 1501));
    CHECK(!strcmp(k2_estop_event(&k, 103, 1, 2000), "k2_triple")); /* rolling last three */
    k2_estop_init(&k, 0);
    for (int i = 0; i < 10; i++) { CHECK(!k2_estop_event(&k, 104, 1, i)); CHECK(!k2_estop_event(&k, 105, 1, i)); }
    CHECK(!k.npress && !k.held && !k.reports_release);
    CHECK(!k2_estop_event(&k, 103, 0, 0) && k.reports_release);
    CHECK(!k2_estop_event(&k, 103, 1, 1));
    CHECK(!k2_estop_event(&k, 104, 0, 2) && k.held); /* rotation release cannot end hold */
    CHECK(!strcmp(k2_estop_tick(&k, 1001), "k2_hold"));
    ctl_t c; ctl_init(&c, NULL); ctl_set_allowed(&c, 1);
    ctl_estop(&c, "k2_hold", 1001); CHECK(c.estopped && !c.allowed && !strcmp(c.stop_reason, "k2_hold"));
}

static void test_ctl_edges(void)
{
    ctl_t c; ctl_init(&c, NULL); char r[4096], q[1024]; ctl_set_allowed(&c, 1);
    /* Maximum UTF-8 summary still fits the fixed status frame. */
    char emoji[481]; for (int i = 0; i < 120; i++) memcpy(emoji + i * 4, "\xf0\x9f\x98\x80", 4); emoji[480] = 0;
    snprintf(q, sizeof q, "{\"op\":\"ui.agent\",\"state\":\"awaiting_confirm\",\"input\":\"hid\",\"summary\":\"%s\",\"progress\":100}", emoji);
    CHECK(H(&c, 0, 0, q, r) > 0 && has(r, "\"ok\":true"));
    const char *status_req = "{\"op\":\"status\",\"id\":999999999}";
    CHECK(ctl_handle_line(&c, 0, status_req, strlen(status_req), 123456789, r, CTL_LINE_MAX) > 0 && has(r, "\"ok\":true"));
    CHECK(strlen(r) < CTL_LINE_MAX);
    screen_req(&c, 0, 0, "{\"op\":\"ui.agent\",\"state\":\"idle\",\"input\":\"hid\",\"summary\":\"x\",\"progress\":0.5}", "bad_args");
    screen_req(&c, 0, 0, "{\"op\":\"confirm.request\",\"cid\":1,\"summary\":\"x\",\"risk\":\"low\",\"timeout_ms\":1e3}", "bad_args");
    CHECK(H(&c, 0, 0, "{\"op\":\"lease.acquire\",\"ttl_ms\":\"bad\"}", r) > 0 && has(r, "bad_ttl"));
    const char nul[] = "{\"op\":\"status\"}\0 ";
    CHECK(ctl_handle_line(&c, 0, nul, sizeof nul - 1, 0, r, sizeof r) > 0 && has(r, "bad_request"));
    c.screen.next_id = 999999990;
    for (int i = 0; i < 8; i++) screen_req(&c, 0, 0, "{\"op\":\"screen.page.create\",\"name\":\"x\"}", "page_id");
    const char *layout_req = "{\"op\":\"screen.layout\"}";
    CHECK(ctl_handle_line(&c, 0, layout_req, strlen(layout_req), 0, r, CTL_LINE_MAX) > 0);
    ctl_screen_grant(&c, 1000, 0);
    screen_req(&c, 0, 0, "{\"op\":\"screen.draw\",\"page_id\":0,\"fb\":20,\"x\":0,\"y\":0,\"w\":1,\"h\":1,\"fmt\":\"rgb565\",\"bytes\":2}", "reserved");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.delete\",\"page_id\":0}", "forbidden");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.create\",\"name\":\"\"}", "bad_args");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.show\",\"page_id\":1}", "\"ok\":true");
    ctl_key_event(&c, 3, 4, 0, 0); CHECK(!c.estopped);
    ctl_key_event(&c, 3, 4, 1, 0); CHECK(c.estopped && c.screen.current == 0 && !c.screen.grant_until);
    ctl_screen_grant(&c, 1000, 0); CHECK(!c.screen.grant_until);
    ctl_set_allowed(&c, 1); ctl_screen_grant(&c, 120001, 0); CHECK(!c.screen.grant_until);
    /* K2 option is enforced before entering the pure state machine, independent of GD32 writes. */
    kbd_t k; memset(&k, 0, sizeof k); k.host_fd = -1; k.ctl = &c;
    uint8_t press[] = {0x16, 1, 103, 103, 0, 0}, rotate[] = {0x16, 1, 104, 104, 0, 0};
    for (int i = 0; i < 3; i++) kbd_on_gd32(&k, press, sizeof press);
    CHECK(!c.estopped && !k.k2.npress);
    k.k2_estop = 1;
    for (int i = 0; i < 3; i++) kbd_on_gd32(&k, rotate, sizeof rotate);
    CHECK(!c.estopped && !k.k2.npress);
    for (int i = 0; i < 3; i++) kbd_on_gd32(&k, press, sizeof press);
    CHECK(c.estopped && !strcmp(c.stop_reason, "k2_triple"));
}

/* Revocation must cancel actions that the gadget has not yet accepted. */
static void test_ctl_backpressure(void)
{
    for (int mode = 0; mode < 7; mode++) for (int sent_down = 0; sent_down < 2; sent_down++) {
        int p[2]; CHECK(nb_pipe(p) == 0);
        hid_t h; hid_init(&h, "/nonexistent", NULL); hid_set_scroll(&h, 1); hid_attach_fd(&h, p[1]);
        uint8_t rep[64][9]; rd_reports_n(p[0], rep, 64, 8);
        ctl_t c; ctl_init(&c, &h); ctl_set_allowed(&c, 1); char r[4096], q[256];
        CHECK(H(&c, 0, 0, "{\"op\":\"lease.acquire\",\"ttl_ms\":1000}", r) > 0); long t = tok_of(r);
        hid_key(&h, 4, 1); hid_pointer_abs(&h, (uint8_t)sent_down, 30, 40); rd_reports_n(p[0], rep, 64, 8);
        char filler[256]; memset(filler, 0x7f, sizeof filler);
        while (write(p[1], filler, sizeof filler) > 0) {}
        while (write(p[1], filler, 1) > 0) {}
        CHECK(errno == EAGAIN);
        hid_pointer_abs(&h, 1, 1000, 2000); hid_pointer_abs(&h, 0, 1000, 2000); hid_scroll(&h, 3, -2);
        CHECK(h.ptr_n == 3 && h.ptr_btn == 0 && h.ptr_sent[1] == sent_down);
        if (mode == 0) ctl_estop(&c, "test", 1);
        else if (mode == 1) ctl_tick(&c, 1000);
        else if (mode == 2) ctl_set_allowed(&c, 0);
        else if (mode == 3) ctl_client_gone(&c, 0, 1);
        else if (mode == 4) ctl_human_input(&c, 1);
        else {
            snprintf(q, sizeof q, "{\"op\":\"%s\",\"token\":%ld,\"seq\":1,\"kind\":\"allup\"}", mode == 5 ? "lease.release" : "act", t);
            CHECK(H(&c, 0, 1, q, r) > 0 && has(r, "\"ok\":true"));
        }
        CHECK(h.ptr_n == 1 && !h.ptr_btn);
        while (read(p[0], filler, sizeof filler) > 0) {} /* discard only pre-stop pipe contents */
        hid_service(&h, 1001);
        int n = rd_reports_n(p[0], rep, 64, 8);
        CHECK(n == 2 && rep[0][0] == 1 && !rep[0][1] && !rep[0][3]);
        CHECK(rep[1][0] == 2 && !rep[1][1] && rep[1][2] == 30 && rep[1][4] == 40 && !rep[1][6] && !rep[1][7]);
        CHECK(!h.ptr_n && !h.ptr_sent[1]);
        close(p[0]); close(p[1]);
    }
    /* Even if a rebind lost the queued up, last SENT buttons still require release. */
    int p[2]; CHECK(nb_pipe(p) == 0);
    hid_t h; hid_init(&h, "/nonexistent", NULL); hid_attach_fd(&h, p[1]);
    uint8_t rep[64][9]; rd_reports_n(p[0], rep, 64, 6);
    hid_pointer_abs(&h, 1, 30, 40); rd_reports_n(p[0], rep, 64, 6);
    h.ptr_btn = 0; h.ptr_n = 0;
    CHECK(hid_pointer_release(&h) == 1);
    CHECK(rd_reports_n(p[0], rep, 64, 6) == 1 && rep[0][0] == 2 && !rep[0][1]);
    CHECK(hid_pointer_release(&h) == 0);
    close(p[0]); close(p[1]);
}

static void test_ctl_screen_disconnect(void)
{
    ctl_t c; ctl_init(&c, NULL); ctl_set_allowed(&c, 1); int p[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, p) == 0); c.cfd[0] = p[0];
    for (int i = 0; i < 8; i++) screen_req(&c, 0, 0, "{\"op\":\"screen.page.create\",\"name\":\"work\"}", "page_id");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.show\",\"page_id\":3}", "\"ok\":true");
    screen_req(&c, 0, 0, "{\"op\":\"screen.page.create\",\"name\":\"full\"}", "busy");
    ctl_client_gone(&c, 0, 1); close(p[1]); CHECK(c.screen.current == CTL_SCREEN_SYSTEM);
    for (int i = 3; i < CTL_SCREEN_PAGES; i++) CHECK(!c.screen.page[i].used);
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, p) == 0); c.cfd[0] = p[0]; /* reused connection slot */
    screen_req(&c, 0, 2, "{\"op\":\"screen.page.create\",\"name\":\"again\"}", "\"page_id\":11");
    screen_req(&c, 0, 2, "{\"op\":\"screen.page.show\",\"page_id\":3}", "not_found");
    ctl_client_gone(&c, 0, 3); close(p[1]);
}

static void test_ctl_json_edges(void)
{
    ctl_t c; ctl_init(&c, NULL); char r[4096], q[1024], text[16];
    const char *pair = "{\"s\":\"\\uD83D\\uDE00\"}";
    CHECK(ctl_json_str(pair, strlen(pair), "s", text, sizeof text) == 4 && !strcmp(text, "\xf0\x9f\x98\x80"));
    const char *cases[] = {"{\"s\":\"\\uD83D\"}", "{\"s\":\"\\uDE00\"}", "{\"s\":\"\\uD83D\\u1234\"}"};
    for (size_t i = 0; i < sizeof cases / sizeof *cases; i++) CHECK(ctl_json_str(cases[i], strlen(cases[i]), "s", text, sizeof text) < 0);
    const char *hex = "{\"s\":\"\\u00aF\"}";
    CHECK(ctl_json_str(hex, strlen(hex), "s", text, sizeof text) == 2 && !strcmp(text, "\xc2\xaf"));
    for (int pos = 0; pos < 4; pos++) for (int b = 0x10; b <= 0x19; b++) {
        strcpy(q, "{\"op\":\"status\",\"s\":\"\\u0061\"}"); strstr(q, "0061")[pos] = (char)b;
        CHECK(H(&c, 0, 0, q, r) > 0 && has(r, "bad_request"));
    }
    const char *ints[] = {"999999999", "-999999999", "1000000000", "-1000000000", "1.0", "1e2"};
    for (int i = 0; i < 6; i++) {
        snprintf(q, sizeof q, "{\"n\":%s}", ints[i]); long v = 0;
        int rc = ctl_json_int(q, strlen(q), "n", &v);
        CHECK(i < 2 ? rc == 0 && v == (i ? -999999999L : 999999999L) : rc < 0);
    }
    const char *bad_ids[] = {"-1", "-999999999", "1000000000", "-1000000000", "1.0", "1e2", "\"1\"", "null"};
    for (size_t i = 0; i < sizeof bad_ids / sizeof *bad_ids; i++) {
        snprintf(q, sizeof q, "{\"op\":\"status\",\"id\":%s}", bad_ids[i]);
        CHECK(H(&c, 0, 0, q, r) > 0 && has(r, "bad_request") && !has(r, "\"id\""));
    }
    CHECK(H(&c, 0, 0, "{\"op\":\"status\",\"id\":0}", r) > 0 && has(r, "\"id\":0"));
    strcpy(q, "{\"op\":\"status\"");
    for (int i = 0; i < 24; i++) {
        size_t at = strlen(q); snprintf(q + at, sizeof q - at, ",\"k%d\":0", i);
        if (i < 22) continue;
        strcat(q, "}");
        CHECK(H(&c, 0, 0, q, r) > 0 && has(r, i == 22 ? "\"ok\":true" : "bad_request"));
        q[strlen(q) - 1] = 0;
    }
}

static void test_ctl_long_uptime(void)
{
    const int64_t base = INT64_C(2147483647) - 100;
    ctl_t c; ctl_init(&c, NULL); ctl_set_allowed(&c, 1); char r[4096]; int p[2];
    CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, p) == 0); c.cfd[0] = p[0];
    CHECK(H(&c, 0, base, "{\"op\":\"lease.acquire\",\"ttl_ms\":1000}", r) > 0 && c.expiry_ms == base + 1000);
    CHECK(ctl_timeout_ms(&c, base + 999, 250) == 1);
    CHECK(H(&c, 0, base + 999, "{\"op\":\"status\"}", r) > 0 && has(r, "\"ttl_left_ms\":1"));
    ctl_tick(&c, base + 1000); CHECK(!c.token); event_has(p[1], "expired");
    CHECK(H(&c, 0, base, confirm_req, r) > 0 && c.confirm.deadline_ms == base + 30000);
    CHECK(H(&c, 0, base, "{\"op\":\"status\"}", r) > 0 && has(r, "\"deadline_ms\":2147513547"));
    ctl_confirm_shown(&c, base); ctl_confirm_key(&c, 1, base + 299); CHECK(c.confirm.pending);
    ctl_tick(&c, base + 29999); CHECK(c.confirm.pending && ctl_timeout_ms(&c, base + 29999, 250) == 1);
    ctl_tick(&c, base + 30000); CHECK(!c.confirm.pending); event_has(p[1], "timeout");
    screen_req(&c, 0, base, "{\"op\":\"screen.page.create\",\"name\":\"x\"}", "page_id");
    for (int i = 0; i < 10; i++) screen_text(&c, base, 1, "rendered\":false");
    screen_text(&c, base + 999, 1, "rate_limited"); screen_text(&c, base + 1000, 1, "rendered\":false");
    ctl_screen_grant(&c, 1000, base); CHECK(c.screen.grant_until == base + 1000);
    screen_req(&c, 0, base + 999, "{\"op\":\"screen.text\",\"page_id\":0,\"fb\":2,\"text\":\"x\",\"size\":1,\"fg\":0,\"bg\":0}", "rendered\":false");
    screen_req(&c, 0, base + 1000, "{\"op\":\"screen.text\",\"page_id\":0,\"fb\":2,\"text\":\"x\",\"size\":1,\"fg\":0,\"bg\":0}", "forbidden");
    CHECK(!c.screen.grant_until);
    for (int fb = 1; fb <= 3; fb++) for (int i = 0; i < 10; i++) screen_text(&c, base + 5000, fb, "rendered\":false");
    screen_text(&c, base + 5999, 4, "rate_limited"); screen_text(&c, base + 6000, 4, "rendered\":false");
    k2_estop_t k; k2_estop_init(&k, 1); CHECK(!k2_estop_event(&k, 103, 1, base));
    CHECK(!k2_estop_tick(&k, base + 999) && k2_estop_timeout(&k, base + 999, 250) == 1);
    CHECK(!strcmp(k2_estop_tick(&k, base + 1000), "k2_hold"));
    k2_estop_init(&k, 0); CHECK(!k2_estop_event(&k, 103, 1, base));
    CHECK(!k2_estop_event(&k, 103, 1, base + 1000));
    CHECK(!strcmp(k2_estop_event(&k, 103, 1, base + 1500), "k2_triple"));
    ctl_client_gone(&c, 0, base + 40000); close(p[1]);
}
