/* mk20-kbd core: glue between the GD32 link, the host link and the screens.
 * Pure functions over fds so tests can drive it with socketpairs/temp dirs. */
#ifndef MK20_KBD_H
#define MK20_KBD_H
#include "k2estop.h"
#include "gd32.h"
#include "gd32io.h"
#include "hid.h"
#include "host.h"
#include "screen.h"
#include <stdint.h>

#define KBD_EVENT_ID 0xFFFFFFFFu   /* KD: device-originated events use id 0xFFFFFFFF */

typedef struct kbd_s {
    struct onboard_s *onboard;
    int64_t (*clock_ms)(void *); void *clock_ctx;
    uint8_t host_out[16384]; size_t host_out_len, host_out_off;
    size_t host_rx_barrier; /* unread bytes queued before the latest route boundary */
    int host_fd;          /* -1 = no host link (local mode) */
    int host_tx_stuck;    /* last reply timed out: next ones fail fast until a write succeeds */
    int64_t host_rx_ms;   /* last host byte read (or the host link opened): host silence backstop */
    int64_t host_flush_ms;/* last silence flush; 0 = none yet */
    int host_silent;      /* the current silence streak has been logged */
    int safe_mode;        /* static rescue panels; no host/UI/agent features */
    struct devstat_s *safe_ds; uint16_t *safe_px; /* safe mode: usb0 IP + web pairing code (worker) */
    char safe_ip[16], safe_pair_raw[7], safe_pair[7]; int safe_stale;
    int local_ui;         /* 1 = draw key feedback locally (no-host mode) */
    screen_t scr;
    host_parser hp;
    uint16_t *canvas;     /* CANVAS_W x CANVAS_H */
    char bl_dir[128];     /* dispdbg dir; "" = backlight disabled */
    const char *version;
    int backlight;        /* last level 0..100, -1 unknown */
    gd32_knob_status knob;
    int gd32_rw;
    hid_t *hid;           /* NULL = no HID output */
    int k2_estop; k2_estop_t k2;
    struct ctl_s *ctl;    /* NULL = no kbd.sock control (default) */
    int ptt_col, ptt_row; /* firmware coords (0-based) of the push-to-talk key; default doc (1,4) */          /* 1 if the tty was opened for writing (knob init allowed) */
    struct {
        uint32_t jpg_ok, jpg_bad, jpg_size, tiles, json_in, json_unsupported, json_bad,
                 key_events, knob_events, echo_frames, other_frames, keycode_nonzero,
                 host_tx, host_tx_errors, host_tx_timeouts, flush_errors, panel_writes, host_flushes;
    } st;
} kbd_t;

int64_t kbd_now(kbd_t *k);
int kbd_init(kbd_t *k, const char *fbdir, const char *bl_dir, const char *version);
int kbd_init_safe(kbd_t *k, const char *fbdir, const char *bl_dir, const char *version);
/* Safe mode: start the status worker (gadget.env IP, /run/mk20/pair-code); panels redraw
 * from its snapshot in kbd_service without filesystem I/O on the loop. */
int kbd_safe_status_start(kbd_t *k);
void kbd_free(kbd_t *k);
/* Loop boundary: expire safety deadlines BEFORE any queued HID output. now_ms must be fresh. */
void kbd_safety_tick(kbd_t *k, int64_t now_ms);
void kbd_ctl_safety(void *ctx, int64_t now_ms); /* ctl pre-dispatch adapter */
void kbd_service(kbd_t *k, int64_t now_ms);
/* One parsed GD32 payload (key event, echo, other). */
void kbd_on_gd32(kbd_t *k, const uint8_t *pl, int len);
void kbd_gd32_cb(void *ctx, const uint8_t *pl, int len);   /* adapter for gd32_knob_init */
/* Raw bytes from the host link. */
void kbd_host_push(kbd_t *k, const uint8_t *d, size_t n);
void kbd_host_feed(kbd_t *k, const uint8_t *d, size_t n);
void kbd_on_host_frame(kbd_t *k, const host_frame *f);
void kbd_host_drain(kbd_t *k);
/* Main-loop only: append without draining or flushing onboard priority events. */
int kbd_host_queue(kbd_t *k, uint32_t id, uint32_t cmd, const void *pl, size_t n);
int kbd_host_send(kbd_t *k, uint32_t id, uint32_t cmd, const void *pl, size_t n);
/* Backlight 0..100 via dispdbg (name=lcd0, command=setbl, param, start). */
int kbd_set_backlight(kbd_t *k, int level);
int kbd_bl_param(int level);
/* Flush both buses. Returns panels written or -1. */
int kbd_flush(kbd_t *k);
/* Boot pattern on all 21 panels (selftest: 21/21 written). */
void kbd_draw_idle(kbd_t *k);
void kbd_draw_key(kbd_t *k, int row, int col, int pressed);
/* JSON helpers (tiny, tolerant). */
int kbd_json_str(const char *s, size_t n, const char *key, char *out, size_t cap);
int kbd_json_int(const char *s, size_t n, const char *key, long *v);
#define KBD_HOST_TX_MS 200   /* legacy wait; with ctl/K2 safety, host sends never wait */
/* Host silence backstop (1.6.1, incident 2026-10-23): with the host fd open and no host byte read for
 * KBD_HOST_SILENCE_MS, tcflush(host_fd, TCIFLUSH) at most once per KBD_HOST_SILENCE_MS while the silence lasts
 * (counted in st.host_flushes, logged once per streak). It only discards host->device input the kernel holds but
 * never delivered (a stalled pty flip buffer); bytes already readable are left alone. Never touches the GD32, HID,
 * e-stop or output paths. kbd_host_rx(): host bytes were read / the link opened. kbd_host_silence(): 0 = nothing,
 * 1 = flushed, 2 = flushed and logged (first flush of a streak). */
#define KBD_HOST_SILENCE_MS 5000
void kbd_host_rx(kbd_t *k, int64_t now_ms);
int kbd_host_silence(kbd_t *k, int64_t now_ms);
int kbd_write_status(kbd_t *k, const char *path);
#endif
