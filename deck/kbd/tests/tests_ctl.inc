/* A1: HID scroll descriptor variant, pointer release, kbd.sock control (lease / e-stop). Included by test_kbd.c. */

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
