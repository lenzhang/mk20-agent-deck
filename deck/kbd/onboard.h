/* U2 compositor. Transport and renderer share one route/epoch gate. */
#ifndef MK20_ONBOARD_H
#define MK20_ONBOARD_H
#include "ui.h"
struct kbd_s;
enum onboard_route { ONBOARD_HOME, ONBOARD_LS, ONBOARD_L5, ONBOARD_L1, ONBOARD_L4, ONBOARD_AGENT };
enum onboard_health { HOST_OFFLINE, HOST_PROBING, HOST_ONLINE, HOST_SUSPECT };
typedef struct onboard_s onboard_t;
int onboard_enable(struct kbd_s *k);
void onboard_free(struct kbd_s *k);
void onboard_tick(struct kbd_s *k, int64_t now);
void onboard_service(struct kbd_s *k, int64_t now);
void onboard_route(struct kbd_s *k, enum onboard_route route);
int onboard_input(struct kbd_s *k, int row, int col, int knob, int down, int keycode, int64_t now);
int onboard_timeout(struct kbd_s *k, int64_t now, int dflt);
void onboard_loss(struct kbd_s *k);
/* Main-loop only: queue pending releases before taps; -1 if still backpressured. */
int onboard_host_flush(struct kbd_s *k);
int onboard_json(struct kbd_s *k, const char *method, const char *s, size_t n, char *result, size_t cap);
void onboard_jpeg(struct kbd_s *k, const uint8_t *data, size_t n);
int onboard_tile_allowed(struct kbd_s *k, int fb, int y, int h);
int onboard_tile(struct kbd_s *k, int fb, int x, int y, int w, int h, const uint16_t *px);
void onboard_valid_frame(struct kbd_s *k, int64_t now);
const ui_t *onboard_ui(const struct kbd_s *k);
enum onboard_route onboard_current(const struct kbd_s *k);
enum onboard_health onboard_health(const struct kbd_s *k);
/* Status probe roots (tests/simulator); production uses devstat_init_env(). */
void onboard_status_root(struct kbd_s *k, const char *root, const char *pair_path, const char *version, const char *rel);
struct devstat_s;
void onboard_status_set(struct kbd_s *k, struct devstat_s *d); /* takes ownership; starts the worker */
/* Tests only (blocking): one full status cycle published and copied. */
int onboard_status_wait(struct kbd_s *k, int timeout_ms);
#endif
