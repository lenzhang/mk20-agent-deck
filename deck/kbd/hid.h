/* HID output on /dev/hidg0 (merged from deck/router/mk20_router.c, r120 fixes kept).
 * Gadget descriptor (gadget.sh): report 1 = keyboard (mods, reserved, 6 keys; 9 B with id),
 * report 2 = absolute pointer (3 buttons, X/Y 0..32767; 6 B with id), report_length 9.
 * Rules (r120):
 *  - all-keys-up on open (a crashed predecessor may have left RAlt held), on exit/EOF/signal;
 *  - state changes only on a fully accepted write; undelivered state is re-sent (dirty);
 *  - non-EAGAIN errors throttle retries to a 100 ms tick (no spin on an unbound gadget);
 *  - NEVER hold the fd across gadget teardown: when the stop file exists (gadget.sh router_stop
 *    touches it before any UDC/configfs change) or hid_request_close() was called (SIGUSR1),
 *    send a bounded all-up and close; reopen only after the stop file is gone. */
#ifndef MK20_HID_H
#define MK20_HID_H
#include <stdint.h>

#define HID_DEFAULT_PATH "/dev/hidg0"
#define HID_DEFAULT_STOP_FILE "/tmp/mk20-router-stop"
#define HID_MOD_RALT 0x40
#define HID_KBD_LEN 9
#define HID_PTR_LEN 6

typedef struct {
    char path[128], stop_file[128];
    int fd;
    uint8_t mods, keys[6];        /* desired keyboard state */
    uint8_t sent[HID_KBD_LEN];    /* last report the gadget accepted */
    int dirty, hard_fail;
    uint8_t ptrq[16][HID_PTR_LEN]; int ptr_head, ptr_n;   /* pointer reports, FIFO (button edges never dropped) */
    volatile int close_req;
    long next_open_ms, last_hard_ms;
    unsigned opens, closes, writes, eagain, errors, ptr_dropped;
} hid_t;

void hid_init(hid_t *h, const char *path, const char *stop_file);
/* Housekeeping: honours stop file / close request, (re)opens when allowed, re-sends dirty state.
 * Call on every loop tick and when the fd polls writable. */
void hid_service(hid_t *h, long now_ms);
int hid_attach_fd(hid_t *h, int fd);   /* tests: use an existing non-blocking fd (sends all-up) */
void hid_set_mod(hid_t *h, uint8_t mod, int on);
int hid_key(hid_t *h, uint8_t usage, int down);         /* -1 if 6 keys already down */
int hid_pointer_abs(hid_t *h, uint8_t buttons, uint16_t x, uint16_t y);
/* Bounded all-keys-up (default ~1.5 s, under gadget.sh's SIGTERM->SIGKILL window). 0 = delivered. */
int hid_release_all(hid_t *h, int timeout_ms);
void hid_close(hid_t *h, int release);
void hid_request_close(hid_t *h);       /* async-signal-safe */
int hid_wants_pollout(const hid_t *h);  /* dirty and no hard failure */
#endif
