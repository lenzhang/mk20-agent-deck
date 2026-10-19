/* Host-only foundation; no secret reads or transport operations. C99. */
#ifndef MK20_UICFG_H
#define MK20_UICFG_H
#include <stddef.h>
#include <sys/types.h>
typedef struct { const char *id; int vision, tools; } ui_model_t;
typedef struct {
    const char *id, *label, *base_url;
    int domestic, supported;
    const ui_model_t *models;
    size_t model_count;
} ui_provider_t;
extern const ui_provider_t UI_PROVIDERS[];
extern const size_t UI_PROVIDER_COUNT;
typedef struct {
    int provider, model, wifi, proxy_port, brightness, volume;
    char proxy_host[254], wifi_proxy[300];
} uicfg_t;
void uicfg_defaults(uicfg_t *c);
int uicfg_valid(const uicfg_t *c);
/* Fault hook: return -1/errno to fail; WRITE may return a short count or EINTR.
 * Called before every corresponding syscall, including marker/parent durability.
 * PARENT_FSYNC_SYSCALL is a test seam after DIR_FSYNC: -1/errno substitutes
 * the parent fsync syscall result, including its existing-ancestor exceptions. */
enum uicfg_op { UICFG_WRITE, UICFG_FSYNC, UICFG_RENAME, UICFG_DIR_FSYNC,
                UICFG_PARENT_FSYNC_SYSCALL };
typedef ssize_t (*uicfg_fault_fn)(void *, enum uicfg_op, size_t);
typedef struct { uicfg_fault_fn fault; void *ctx; } uicfg_hooks_t;
/* Failure after mutation leaves .apply-in-progress: consumers must block reload.
 * Consumer gate/main-loop integration belongs to U3/U2. Re-Apply repairs full pair. */
int uicfg_apply(const char *root, const uicfg_t *c, const uicfg_hooks_t *hooks);
int uicfg_serialize(const uicfg_t *c, char *net, size_t nn, char *provider, size_t pn);
int uicfg_quote(const char *src, char *dst, size_t cap);
#endif
