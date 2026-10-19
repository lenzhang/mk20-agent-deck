#define _POSIX_C_SOURCE 200809L
#include <sys/socket.h>
#include <errno.h>
#include <assert.h>
#include <string.h>
#include <stdio.h>
/* Trap every socket, including any DNS socket, before it can reach the host.
 * Include the real RFB implementation so this regression needs no production seam. */
static unsigned streams, dns_sockets;
static int trapped_socket(int family, int type, int protocol)
{
    (void)protocol; assert(family == AF_INET || family == AF_INET6);
    if (type == SOCK_DGRAM) dns_sockets++; else { assert(type == SOCK_STREAM); streams++; }
    errno=EACCES; return -1;
}
#define socket trapped_socket
#include "../src/rfb.c"
#undef socket
int main(void)
{
    const char *invalid[]={"vnc.example", "localhost.example", "127.1", "[::1]", "fe80::1%en0", "127.0.0.1:5900", ""};
    const char *valid[]={"127.0.0.1", "192.0.2.1", "::1", "2001:db8::1", "localhost"};
    for (unsigned i=0;i<sizeof invalid/sizeof *invalid;i++) {
        rfb_options_t o={.host=invalid[i],.port=5900,.timeout_ms=100};rfb_t *c=NULL;const char *error=NULL;
        int rc=rfb_open(&c,&o,&error);
        if (rc>=0 || c || strcmp(error,"vnc_host_not_literal")) fprintf(stderr,"host %s: rc=%d error=%s streams=%u dns=%u\n",invalid[i],rc,error,streams,dns_sockets);
        assert(rc<0 && !c && !strcmp(error,"vnc_host_not_literal"));
        assert(!streams && !dns_sockets);
    }
    for (unsigned i=0;i<sizeof valid/sizeof *valid;i++) {
        rfb_options_t o={.host=valid[i],.port=5900,.timeout_ms=100};rfb_t *c=NULL;const char *error=NULL;
        assert(rfb_open(&c,&o,&error)<0 && !c && !strcmp(error,"connect"));
        assert(streams==i+1 && !dns_sockets);
    }
    puts("RFB literal-only: hostnames rejected before socket, zero DNS sockets");return 0;
}
