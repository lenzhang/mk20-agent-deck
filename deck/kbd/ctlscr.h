/* D8 page/rate/permission rules only; pixel transport and rendering belong to A7. */
#ifndef MK20_CTLSCR_H
#define MK20_CTLSCR_H
#include <stddef.h>
#include <stdint.h>
#define CTL_SCREEN_SYSTEM 0
#define CTL_SCREEN_CONTROL 1
#define CTL_SCREEN_USER 2
#define CTL_SCREEN_AGENT_MAX 8
#define CTL_SCREEN_PAGES (3 + CTL_SCREEN_AGENT_MAX)
#define CTL_SCREEN_ESTOP_ROW 3
#define CTL_SCREEN_ESTOP_COL 4
#define CTL_SCREEN_GRANT_MAX 120000
#define CTL_SCREEN_FB_RATE 10
#define CTL_SCREEN_RATE 30
#define CTL_SCREEN_BYTES_MAX 65536

typedef struct {
    int used, id, client; /* builtin ids 0..2; client -1 after disconnect, never inherited by a new connection */
    char name[129]; uint32_t bound; char action[20][481];
} ctl_page_t;
typedef struct {
    ctl_page_t page[CTL_SCREEN_PAGES]; int current, next_id;
    long grant_until;
    struct { long at; int fb; } writes[CTL_SCREEN_RATE]; int nwrites;
} ctl_screen_t;
struct ctl_s;
void ctl_screen_init(struct ctl_s *c);
void ctl_screen_reset(struct ctl_s *c); /* disallow/estop: keep pages, drop binds/grant, system page */
void ctl_screen_gone(struct ctl_s *c, int ci);
void ctl_screen_grant(struct ctl_s *c, long ttl_ms, long now_ms); /* keyboard only */
void ctl_key_event(struct ctl_s *c, int row, int col, int pressed, long now_ms);
int ctl_screen_handle(struct ctl_s *c, int ci, const char *op, const char *s, size_t n,
                      long now, long id, char *r, size_t cap, const char **error);
#endif
