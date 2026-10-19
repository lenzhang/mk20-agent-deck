#ifndef MK20_MEM_H
#define MK20_MEM_H
#include <stddef.h>
/* Requested heap bytes, including our allocation headers; allocator/RSS overhead is separate. */
#define MEM_LIMIT (14u * 1024u * 1024u)
typedef struct { size_t live, high; } mem_t;
void *mem_alloc(mem_t *m, size_t n);
void mem_free(mem_t *m, void *p);
void mem_wipe(void *p, size_t n);
#endif
