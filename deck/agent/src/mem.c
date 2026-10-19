#include "mem.h"
#include <stdint.h>
#include <stdlib.h>
typedef union { size_t n; max_align_t align; } header_t;
void *mem_alloc(mem_t *m, size_t n)
{
    if (n > MEM_LIMIT - sizeof(header_t) || m->live > MEM_LIMIT - n - sizeof(header_t)) return NULL;
    header_t *h = calloc(1, sizeof *h + n); if (!h) return NULL;
    h->n = sizeof *h + n; m->live += h->n; if (m->live > m->high) m->high = m->live;
    return h + 1;
}
void mem_free(mem_t *m, void *p)
{
    if (p) { header_t *h = (header_t *)p - 1; m->live -= h->n; free(h); }
}
void mem_wipe(void *p, size_t n) { volatile unsigned char *b = p; while (n--) *b++ = 0; }
