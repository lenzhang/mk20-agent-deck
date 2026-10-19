#ifndef MK20_KBDC_H
#define MK20_KBDC_H
#include "loop.h"
#include "calib.h"
typedef struct {
    int fd, stopped, allowed, notified_allowed, vnc_capable, releasing, require_off;
    unsigned id, token, seq, ttl;
    int64_t expiry, next_status, action_deadline;
    char in[1024], error[40]; size_t used;
    kbd_event_t events[64]; unsigned head, count;
    calib_t calibration;
} kbdc_t;
int64_t kbdc_now(void);
int kbdc_open(kbdc_t *, const char *path);
int kbdc_connected(kbdc_t *, int owned_fd);
void kbdc_close(kbdc_t *);
int kbdc_poll(kbdc_t *, kbd_event_t *);
int kbdc_lease(kbdc_t *, lease_op_t);
int kbdc_confirm(kbdc_t *, unsigned cid, const char *summary, unsigned timeout_ms);
int kbdc_pair_display(kbdc_t *, const char code[7]);
int kbdc_ui(kbdc_t *, const char *state, int vnc, unsigned progress);
int kbdc_execute(kbdc_t *, const action_t *, int vnc, int64_t deadline, loop_guard_t, void *);
#endif
