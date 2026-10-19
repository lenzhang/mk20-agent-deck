#define _POSIX_C_SOURCE 200809L
#include "dns.h"
#include "net.h"
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static unsigned checks;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr,"dns line %d: %s (errno=%d)\n",__LINE__,#x,errno); abort(); } } while (0)
static void p16(unsigned char *b, unsigned v) { b[0]=(unsigned char)(v>>8); b[1]=(unsigned char)v; }
static unsigned u16(const unsigned char *b) { return (unsigned)b[0]*256+b[1]; }
static size_t putname(unsigned char *b, const char *s)
{
    size_t n=0; while (*s) { size_t k=strcspn(s,"."); b[n++]=(unsigned char)k; memcpy(b+n,s,k); n+=k; s+=k; if (*s) s++; } b[n++]=0; return n;
}
static size_t question(unsigned char *b, unsigned type)
{
    memset(b,0,DNS_PACKET_MAX); p16(b,123); p16(b+2,0x8180); p16(b+4,1);
    size_t n=12+putname(b+12,"test.example"); p16(b+n,type); p16(b+n+2,1); return n+4;
}
static size_t rr(unsigned char *b, size_t n, const char *owner, unsigned type, const char *alias)
{
    n+=putname(b+n,owner); p16(b+n,type); p16(b+n+2,1); memset(b+n+4,0,4);
    size_t data=n+10, len=type==5 ? putname(b+data,alias) : type==1 ? 4 : 16;
    p16(b+n+8,(unsigned)len);
    if (type!=5) CHECK(inet_pton(type==1?AF_INET:AF_INET6,type==1?"192.0.2.7":"2001:db8::7",b+data)==1);
    return data+len;
}
static void packets(void)
{
    unsigned char b[DNS_PACKET_MAX], copy[DNS_PACKET_MAX]; dns_addresses_t out;
    size_t n=question(b,1); p16(b+6,3); n=rr(b,n,"two.example",1,NULL);
    n=rr(b,n,"one.example",5,"two.example"); n=rr(b,n,"TEST.example",5,"one.example");
    CHECK(!dns_parse(b,n,123,"test.example",1,443,&out) && out.n==1);
    CHECK(ntohs(((struct sockaddr_in *)&out.addr[0])->sin_port)==443);
    for (size_t i=0;i<n;i++) CHECK(dns_parse(b,i,123,"test.example",1,443,&out)!=0);
    memcpy(copy,b,n); copy[0]^=1; CHECK(dns_parse(copy,n,123,"test.example",1,443,&out)==-2);
    memcpy(copy,b,n); copy[2]|=2; CHECK(dns_parse(copy,n,123,"test.example",1,443,&out)==-1);
    memcpy(copy,b,n); copy[2]=1; CHECK(dns_parse(copy,n,123,"test.example",1,443,&out)==-2);
    memcpy(copy,b,n); copy[2]|=8; CHECK(dns_parse(copy,n,123,"test.example",1,443,&out)==-2);
    CHECK(dns_parse(b,n,123,"other.example",1,443,&out)==-2);
    CHECK(dns_parse(b,n,123,"test.example",28,443,&out)==-2);
    memcpy(copy,b,n); copy[12]=0xc0; copy[13]=12; CHECK(dns_parse(copy,n,123,"test.example",1,443,&out)==-1);
    n=question(b,1); p16(b+6,1); b[n]=0xc0; b[n+1]=(unsigned char)n; n+=2; /* self pointer */
    memset(b+n,0,10); n+=10; CHECK(dns_parse(b,n,123,"test.example",1,443,&out)==-1);
    n=question(b,1); p16(b+2,0x8183); CHECK(dns_parse(b,n,123,"test.example",1,443,&out)==-3);
    p16(b+2,0x8182); CHECK(dns_parse(b,n,123,"test.example",1,443,&out)==-1);
    n=question(b,28); p16(b+6,1); n=rr(b,n,"test.example",28,NULL);
    CHECK(!dns_parse(b,n,123,"test.example",28,80,&out) && out.n==1 && out.addr[0].ss_family==AF_INET6);
    n=question(b,1); p16(b+6,1); b[n++]=0xc0; b[n++]=12;
    p16(b+n,1);p16(b+n+2,1);memset(b+n+4,0,4);p16(b+n+8,4);n+=10;
    CHECK(inet_pton(AF_INET,"192.0.2.7",b+n)==1);n+=4;
    CHECK(!dns_parse(b,n,123,"TEST.example",1,443,&out) && out.n==1);
    size_t qend=12+strlen("test.example")+2;
    p16(b+qend+2,3);CHECK(dns_parse(b,n,123,"test.example",1,443,&out)==-2);
    n=question(b,1);p16(b+6,2);size_t alias_at=n;n=rr(b,n,"alias.example",1,NULL);
    n+=putname(b+n,"test.example");p16(b+n,5);p16(b+n+2,1);memset(b+n+4,0,4);p16(b+n+8,2);n+=10;
    p16(b+n,0xc000+(unsigned)alias_at);n+=2;
    CHECK(!dns_parse(b,n,123,"test.example",1,443,&out) && out.n==1);
    n=question(b,1); p16(b+10,1); n=rr(b,n,"test.example",1,NULL);
    CHECK(!dns_parse(b,n,123,"test.example",1,443,&out) && !out.n); /* additional not trusted */
    n=question(b,1); p16(b+6,2); n=rr(b,n,"test.example",5,"one.example"); n=rr(b,n,"one.example",5,"test.example");
    CHECK(dns_parse(b,n,123,"test.example",1,443,&out)==-1);
    for (unsigned depth=8;depth<=9;depth++) {
        n=question(b,1); p16(b+6,depth+1); char owner[32]="test.example", alias[32];
        for (unsigned i=0;i<depth;i++) { snprintf(alias,sizeof alias,"n%u.example",i); n=rr(b,n,owner,5,alias); strcpy(owner,alias); }
        n=rr(b,n,owner,1,NULL); CHECK((dns_parse(b,n,123,"test.example",1,443,&out)==0)==(depth==8));
    }
    /* Every single byte mutation and deterministic random hostile datagrams. */
    for (size_t i=0;i<n;i++) { memcpy(copy,b,n); copy[i]^=255; (void)dns_parse(copy,n,123,"test.example",1,443,&out); }
    unsigned x=7;
    for (size_t len=0;len<sizeof b;len+=7) { for (size_t i=0;i<len;i++) { x=x*1664525+1013904223; b[i]=(unsigned char)(x>>24); } (void)dns_parse(b,len,123,"test.example",1,443,&out); }
    const char *bad[]={"", "a.","a..b","-a.b","a-.b","a_b","a b","a/b","a:53"};
    for (size_t i=0;i<sizeof bad/sizeof *bad;i++) CHECK(!dns_host_valid(bad[i]));
    char host[255]; memset(host,'a',sizeof host); host[63]=host[127]=host[191]='.'; host[253]=0;
    CHECK(dns_host_valid(host)); host[253]='a';host[254]=0;CHECK(!dns_host_valid(host)); host[63]='a';host[253]=0;CHECK(!dns_host_valid(host));
}
typedef struct { int fd[2], spoof; unsigned count[2], type[8], types; int mode; } fake_t;
static int udp_socket(unsigned *port)
{
    int fd=socket(AF_INET,SOCK_DGRAM,0); CHECK(fd>=0);
    struct sockaddr_in a={.sin_family=AF_INET,.sin_addr={htonl(0x7f000001)}};
    if (bind(fd,(void *)&a,sizeof a)) { fprintf(stderr,"DNS UDP fixture bind 127.0.0.1: %s\n",strerror(errno)); close(fd); return -1; }
    socklen_t len=sizeof a;CHECK(!getsockname(fd,(void *)&a,&len));*port=ntohs(a.sin_port);CHECK(!fcntl(fd,F_SETFL,O_NONBLOCK));return fd;
}
static int service(void *v)
{
    fake_t *f=v; unsigned char q[DNS_PACKET_MAX], b[DNS_PACKET_MAX];
    for (int i=0;i<2;i++) {
        struct sockaddr_storage peer; socklen_t len=sizeof peer;
        ssize_t k=recvfrom(f->fd[i],q,sizeof q,0,(void *)&peer,&len); if(k<0) continue;
        f->count[i]++; CHECK(k>=16); unsigned type=u16(q+k-4); if(f->types<8) f->type[f->types++]=type;
        memcpy(b,q,(size_t)k); p16(b+2,0x8180);p16(b+6,0);size_t n=(size_t)k;
        if(f->mode==6) continue; /* timeout */
        if(f->mode==3) p16(b+2,0x8183);
        else if(f->mode==4 && i==0) p16(b+2,0x8182);
        else if(f->mode==7) p16(b+2,0x8380);
        else if(f->mode==8) { b[12]=0xc0;b[13]=12; }
        else if(f->mode!=2 || type==28) {
            if(f->mode==1) { p16(b+6,3); n=rr(b,n,"test.example",5,"alias.example"); n=rr(b,n,"alias.example",5,"end.example");n=rr(b,n,"end.example",type,NULL); }
            else { p16(b+6,1);n=rr(b,n,"test.example",type,NULL); }
        }
        if(f->mode==5) {
            b[0]^=1; CHECK(sendto(f->fd[i],b,n,0,(void *)&peer,len)==(ssize_t)n); b[0]^=1;
            /* Spoofed port carries a different address; only real server wins. */
            b[n-1]=99;CHECK(sendto(f->spoof,b,n,0,(void *)&peer,len)==(ssize_t)n);b[n-1]=7;
        }
        CHECK(sendto(f->fd[i],b,n,0,(void *)&peer,len)==(ssize_t)n);
    }
    return 0;
}
static int cancelled(void *v) { (void)v; return 1; }
static int families[8]; static unsigned family_count;
static int count_dns_socket(int family,int type,int protocol)
{
    (void)type; (void)protocol; if (family_count < 8) families[family_count++] = family;
    errno = EACCES; return -1;
}
static void local(void)
{
    char path[]="/tmp/a8-resolv-XXXXXX";int fd=mkstemp(path);CHECK(fd>=0);close(fd);
    dns_options_t o={.resolv_path=path,.test_ports=1};dns_addresses_t out;const char *error;
    CHECK(!dns_resolve("127.0.0.1",443,dns_now_ms()+100,&o,&out,&error)&&out.n==1);
    CHECK(!dns_resolve("::1",443,dns_now_ms()+100,&o,&out,&error)&&out.addr[0].ss_family==AF_INET6);
    CHECK(dns_resolve("test.example",443,dns_now_ms()+100,&o,&out,&error)<0 && strstr(error,"no nameservers"));
    o.service=cancelled;CHECK(dns_resolve("test.example",443,dns_now_ms()+100,&o,&out,&error)<0 && errno==ECANCELED);
    o.service=NULL; o.socket_fn=count_dns_socket;
    FILE *f=fopen(path,"w");CHECK(f);
    fputs("search ignored\n# comment\nnameserver ::1\nnameserver 127.0.0.1 ; comment\nnameserver [2001:db8::53]:5353\nnameserver 192.0.2.123\n",f);fclose(f);
    CHECK(dns_resolve("test.example",443,dns_now_ms()+100,&o,&out,&error)<0);
    CHECK(family_count==6 && families[0]==AF_INET6 && families[1]==AF_INET && families[2]==AF_INET6 && families[3]==AF_INET6);
    unsigned before=family_count;
    CHECK(!dns_resolve("192.0.2.1",443,dns_now_ms()+100,&o,&out,&error) && family_count==before);
    CHECK(dns_resolve("bad.example/",443,dns_now_ms()+100,&o,&out,&error)<0 && family_count==before);
    char longest[254]; memset(longest,'a',253); longest[63]=longest[127]=longest[191]='.'; longest[253]=0;
    CHECK(dns_resolve(longest,443,dns_now_ms()+100,&o,&out,&error)<0);
    unlink(path);
}
static void udp(void)
{
    fake_t f={0};unsigned ports[3];f.fd[0]=udp_socket(&ports[0]);CHECK(f.fd[0]>=0);f.fd[1]=udp_socket(&ports[1]);CHECK(f.fd[1]>=0);f.spoof=udp_socket(&ports[2]);CHECK(f.spoof>=0);
    char path[]="/tmp/a8-udp-resolv-XXXXXX";int fd=mkstemp(path);CHECK(fd>=0);FILE *config=fdopen(fd,"w");CHECK(config);
    fprintf(config,"# fixture only\nsearch ignored.example\nnameserver 127.0.0.1:%u # first\nnameserver 127.0.0.1:%u\n",ports[0],ports[1]);fclose(config);
    dns_options_t o={.resolv_path=path,.test_ports=1,.service=service,.ctx=&f};dns_addresses_t out;const char *error;
    for(int mode=0;mode<=8;mode++) {
        f.mode=mode;f.count[0]=f.count[1]=f.types=0;int64_t start=dns_now_ms();
        int rc=dns_resolve("test.example",443,start+(mode==6?100:1000),&o,&out,&error);
        if(mode==3 || mode>=6) CHECK(rc<0);
        else { CHECK(!rc && out.n>=1); CHECK(f.type[0]==1); CHECK(out.addr[0].ss_family==(mode==2?AF_INET6:AF_INET));
            if(mode==2) CHECK(f.types==2&&f.type[1]==28);
            if(mode==4) CHECK(f.count[0]==2&&f.count[1]==2);
            if(mode!=2) CHECK(out.n==2 && out.addr[1].ss_family==AF_INET6 && f.type[f.types-1]==28);
            if(mode==5) CHECK(((unsigned char *)&((struct sockaddr_in *)&out.addr[0])->sin_addr)[3]==7);
        }
        if(mode==6) CHECK(dns_now_ms()-start>=90&&dns_now_ms()-start<300);
    }
    close(f.fd[0]);close(f.fd[1]);close(f.spoof);unlink(path);
}
int main(int argc,char **argv) { (void)argv;packets();local();if(argc>1)udp();printf("DNS: %u checks passed%s\n",checks,argc>1?" (UDP fixtures included)":" (parser/literals/config)");return 0; }
