#ifdef __APPLE__
#define _DARWIN_C_SOURCE
#endif
#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <stdlib.h>
#include <assert.h>
#include <stdio.h>
static void *watched;
static int wiped;
static void checked_free(void *p);
#define free checked_free
#include "../src/httpd.c"
#undef free
static void checked_free(void *p)
{
    if (p && p == watched) {
        for (size_t i = 0; i < HTTPD_HEADERS+HTTPD_BODY+1; i++) assert(((unsigned char *)p)[i] == 0);
        wiped++;
    }
    free(p);
}
int main(void)
{
    connection_t c = {.fd=-1}; c.in=malloc(HTTPD_HEADERS+HTTPD_BODY+1); assert(c.in);
    memset(c.in,0x51,HTTPD_HEADERS+HTTPD_BODY+1); watched=c.in;
    drop(&c,NULL); assert(wiped==1 && !c.in);
    puts("HTTP receive buffer: drop wipes full allocation"); return 0;
}
