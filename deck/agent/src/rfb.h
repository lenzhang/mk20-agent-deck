#ifndef MK20_RFB_H
#define MK20_RFB_H
#include "scale.h"
#include <stdint.h>
#define RFB_EXT_CLIPBOARD UINT32_C(0xc0a1e5ce)
typedef struct rfb rfb_t;
typedef struct {
    const char *host; unsigned port; const unsigned char *password; size_t password_len;
    int timeout_ms; /* 0: 5000; numeric IPv4/IPv6 or localhost (no unbounded DNS lookup) */
} rfb_options_t;
typedef struct { double connect_ms, auth_ms, fb_ms, finish_ms; unsigned w, h; int extended_clipboard; } rfb_info_t;
int64_t rfb_now_ms(void);
int rfb_open(rfb_t **out, const rfb_options_t *o, const char **error);
/* Takes ownership of an already-connected stream socket (also useful for host IPC tests). */
int rfb_open_connected(rfb_t **out, const rfb_options_t *o, int fd, const char **error);
void rfb_close(rfb_t *c);
const char *rfb_error(const rfb_t *c);
const rfb_info_t *rfb_info(const rfb_t *c);
mem_t *rfb_memory(rfb_t *c);
/* Wire request supports either mode; snapshot always requests full data. */
int rfb_request(rfb_t *c, int incremental);
int rfb_snapshot(rfb_t *c, unsigned max_w, unsigned max_h);
const scale_t *rfb_frame(const rfb_t *c);
/* UTF-8 input, converted only if every code point is Latin-1. Otherwise "unsupported".
 * Extended Clipboard capability never enables UTF-8 sending in A2. */
int rfb_cut_text(rfb_t *c, const char *utf8, size_t n);
#endif
