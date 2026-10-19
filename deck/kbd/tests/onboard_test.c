#include "kbd.h"
#include "ctl.h"
#include "onboard.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <sys/socket.h>
#include <sys/stat.h>
static int checks, fails; static int64_t now;
#define CHECK(x) do { checks++; if (!(x)) { fails++; fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); } } while (0)
static int64_t clock_ms(void *ctx) { (void)ctx; return now; }
/* Let the common 4 Hz producer budget advance before settling a whole page. */
static void pump(kbd_t *k) { now+=250; for (int i=0;i<64;i++) kbd_service(k,now); }
static void key(kbd_t *k,int fb,int down) { uint8_t ev[]={0x16,(uint8_t)down,(uint8_t)((fb-1)/5),(uint8_t)((fb-1)%5),0,0}; kbd_on_gd32(k,ev,6); }
static void knob(kbd_t *k,int slot,int down) { uint8_t ev[]={0x16,(uint8_t)down,(uint8_t)slot,(uint8_t)slot,0,0}; kbd_on_gd32(k,ev,6); }
static void json(kbd_t *k,const char *s) { host_frame f={42,101,(uint32_t)strlen(s),(const uint8_t *)s}; kbd_on_host_frame(k,&f); }
static void req(ctl_t *c,const char *s,const char *want) { char r[4096]; ctl_handle_line(c,0,s,strlen(s),now,r,sizeof r); CHECK(strstr(r,want)!=NULL); }
static uint64_t hash(kbd_t *k,int fb) { uint64_t h=0; const panel_t *p=&PANELS[fb-1]; for (int i=0;i<p->w*p->h;i++) h=h*33+k->scr.pending[fb-1][i]; return h; }
static void hello(kbd_t *k) { json(k,"{\"method\":\"ui.hello\",\"version\":1}"); }
static void beat(kbd_t *k) { json(k,"{\"method\":\"ui.heartbeat\"}"); }
static uint8_t *jpg; static size_t jpg_size;
static void jpeg(kbd_t *k) { host_frame f={99,100,(uint32_t)jpg_size,jpg}; kbd_on_host_frame(k,&f); }
static void finish_decode(kbd_t *k) { /* real worker, fake deadline clock; no real device writer */
    unsigned before=k->st.jpg_ok+k->st.jpg_bad+k->st.jpg_size;
    for (int i=0;i<1000 && before==k->st.jpg_ok+k->st.jpg_bad+k->st.jpg_size;i++) { kbd_service(k,now); usleep(1000); }
    pump(k);
}
static void tile(kbd_t *k,int fb,int y,int height) {
    size_t n=9+(size_t)height*2; uint8_t *p=calloc(1,n); CHECK(p!=NULL);
    p[0]=fb; p[3]=(uint8_t)y; p[4]=(uint8_t)(y>>8); p[5]=1; p[7]=(uint8_t)height;
    memset(p+9,0xff,n-9); host_frame f={77,200,(uint32_t)n,p}; kbd_on_host_frame(k,&f); free(p);
}
static void init(kbd_t *k,ctl_t *c) {
    now=10000; CHECK(!kbd_init(k,"/nonexistent-u2","","test")); CHECK(!onboard_enable(k));
    ctl_init(c,NULL); k->ctl=c; k->clock_ms=clock_ms; k->k2_estop=1; k2_estop_init(&k->k2,1); pump(k);
}
static void offline_and_ownership(void) {
    kbd_t k; ctl_t c; init(&k,&c);
    CHECK(onboard_current(&k)==ONBOARD_HOME && c.screen.current==0 && !c.allowed);
    json(&k,"{\"method\":\"setBacklight\",\"level\":80}"); CHECK(k.backlight==80 && onboard_current(&k)==ONBOARD_HOME);
    json(&k,"{\"method\":\"ui.hello\",\"version\":2}"); CHECK(onboard_health(&k)==HOST_OFFLINE);
    json(&k,"{\"method\":\"ui.heartbeat\"}garbage"); CHECK(onboard_health(&k)==HOST_OFFLINE);
    key(&k,5,1); key(&k,5,0); now+=200; pump(&k);
    CHECK(onboard_current(&k)==ONBOARD_LS && c.screen.current==0);
    key(&k,7,1); CHECK(ui_pending(onboard_ui(&k))); /* draft preserved across helper flap */
    for (int route=0;route<3;route++) {
        onboard_route(&k,(enum onboard_route)route); now+=200; pump(&k);
        uint64_t h[21]; for (int fb=1;fb<=21;fb++) h[fb-1]=hash(&k,fb);
        jpeg(&k); tile(&k,1,0,1); hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); pump(&k);
        CHECK(onboard_current(&k)==(enum onboard_route)route); CHECK(k.st.jpg_ok==0);
        for (int fb=1;fb<=20;fb++) if (route || fb != 1) CHECK(h[fb-1]==hash(&k,fb));
    }
    CHECK(ui_pending(onboard_ui(&k)));
    onboard_route(&k,ONBOARD_LS); now+=300; pump(&k);
    uint64_t stable=hash(&k,1); hello(&k); CHECK(hash(&k,1)==stable);
    onboard_loss(&k); CHECK(hash(&k,1)==stable);
    onboard_route(&k,ONBOARD_L1); now+=200; pump(&k); CHECK(c.screen.current==2);
    onboard_loss(&k); CHECK(onboard_current(&k)==ONBOARD_L1); CHECK(onboard_health(&k)==HOST_OFFLINE);
    key(&k,16,1); key(&k,16,0); CHECK(k.hid==NULL);
    key(&k,5,1); CHECK(onboard_current(&k)==ONBOARD_LS);
    kbd_free(&k);
}
static void health_and_stale(void) {
    kbd_t k; ctl_t c; init(&k,&c); uint64_t reserved_before=hash(&k,20); onboard_route(&k,ONBOARD_L1);
    hello(&k); beat(&k); now+=1000; beat(&k); CHECK(onboard_health(&k)==HOST_PROBING);
    now+=1000; beat(&k); CHECK(onboard_health(&k)==HOST_ONLINE);
    CHECK(!onboard_tile_allowed(&k,1,0,1)); pump(&k);
    uint16_t footer[428*30]; memcpy(footer,k.scr.pending[20]+428*112,sizeof footer);
    jpeg(&k); finish_decode(&k);
    CHECK(hash(&k,20)==reserved_before); CHECK(!memcmp(footer,k.scr.pending[20]+428*112,sizeof footer));
    CHECK(k.st.jpg_ok==1 && onboard_tile_allowed(&k,1,0,1));
    ctl_set_allowed(&c,1); uint64_t host_face=hash(&k,1);
    req(&c,"{\"op\":\"ui.agent\",\"state\":\"acting\",\"input\":\"hid\",\"summary\":\"test\",\"progress\":1}","true");
    pump(&k); CHECK(hash(&k,1)==host_face);
    uint64_t e=hash(&k,20), strip=0; for(int i=112*428;i<142*428;i++) strip+=k.scr.pending[20][i];
    CHECK(!onboard_tile_allowed(&k,20,0,1)); CHECK(!onboard_tile_allowed(&k,21,111,2));
    tile(&k,1,0,1); pump(&k); CHECK(k.scr.pending[0][0]==0xffff);
    CHECK(hash(&k,20)==e); uint64_t after=0; for(int i=112*428;i<142*428;i++) after+=k.scr.pending[20][i]; CHECK(strip==after);
    json(&k,"{\"method\":\"ui.page\",\"page\":\"L4\",\"generation\":5}"); CHECK(onboard_current(&k)==ONBOARD_L4);
    CHECK(!onboard_tile_allowed(&k,1,0,1));
    json(&k,"{\"method\":\"ui.page\",\"page\":\"L1\",\"generation\":4}"); CHECK(onboard_current(&k)==ONBOARD_L4);
    jpeg(&k); onboard_route(&k,ONBOARD_LS); finish_decode(&k); now+=200; pump(&k);
    uint64_t ls=hash(&k,1); CHECK(onboard_current(&k)==ONBOARD_LS); CHECK(!onboard_tile_allowed(&k,1,0,1));
    now+=3000; kbd_safety_tick(&k,now); CHECK(onboard_health(&k)==HOST_SUSPECT); beat(&k); CHECK(onboard_health(&k)==HOST_SUSPECT);
    now+=2000; kbd_safety_tick(&k,now); CHECK(onboard_health(&k)==HOST_OFFLINE); pump(&k); CHECK(hash(&k,1)==ls);
    hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); CHECK(onboard_current(&k)==ONBOARD_LS);
    /* Incomplete old full frame straddles switch. Still acked, never decoded. */
    size_t n=jpg_size+HOST_HDR+4; uint8_t *wire=malloc(n); CHECK(host_encode(123,100,jpg,jpg_size,wire,n)==(int)n);
    onboard_route(&k,ONBOARD_L1); kbd_host_feed(&k,wire,32); onboard_route(&k,ONBOARD_HOME);
    unsigned count=k.st.jpg_ok; kbd_host_feed(&k,wire+32,n-32); pump(&k); CHECK(k.st.jpg_ok==count); free(wire);
    kbd_free(&k);
}
static void safety(void) {
    kbd_t k; ctl_t c; init(&k,&c); onboard_route(&k,ONBOARD_L1);
    knob(&k,103,1); now+=999; kbd_service(&k,now); CHECK(!c.estopped);
    now++; kbd_service(&k,now); CHECK(c.estopped && onboard_current(&k)==ONBOARD_HOME);
    knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_HOME); /* no short toggle */
    ctl_set_allowed(&c,1); kbd_service(&k,now); onboard_route(&k,ONBOARD_L1);
    knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4);
    knob(&k,100,1); now+=1000; kbd_service(&k,now); knob(&k,100,0); CHECK(onboard_current(&k)==ONBOARD_HOME);
    /* Unknown release capability: first hold cannot fire; three presses do. */
    k2_estop_init(&k.k2,0); knob(&k,103,1); now+=1000; kbd_service(&k,now); CHECK(!c.estopped);
    knob(&k,103,1); knob(&k,103,1); CHECK(c.estopped);
    ctl_set_allowed(&c,1); req(&c,"{\"op\":\"lease.acquire\",\"ttl_ms\":200}","token");
    req(&c,"{\"op\":\"confirm.request\",\"cid\":3,\"risk\":\"low\",\"summary\":\"test\",\"timeout_ms\":1000}","true");
    /* A saturated host pipe and slow writer do not postpone TTL/confirmation. */
    int p[2]; CHECK(!pipe(p)); fcntl(p[1],F_SETFL,O_NONBLOCK); fcntl(p[0],F_SETFL,O_NONBLOCK);
    char fill[256]; memset(fill,1,sizeof fill); while(write(p[1],fill,sizeof fill)>0) {} k.host_fd=p[1];
    screen_set_write_delay(&k.scr,100000); CHECK(!screen_start_writers(&k.scr));
    jpeg(&k); now+=200; kbd_service(&k,now); CHECK(!c.token && c.st.expiries==1);
    now+=800; kbd_service(&k,now); CHECK(!c.confirm.pending && !c.confirm.shown);
    CHECK(k.host_out_len>0); /* drain later returns original empty ack id */
    while(read(p[0],fill,sizeof fill)>0) {} kbd_host_drain(&k);
    host_parser parser; host_parser_init(&parser,1024); uint8_t bytes[128]; ssize_t n=read(p[0],bytes,sizeof bytes); CHECK(n>0);
    if(n>0) host_push(&parser,bytes,(size_t)n);
    host_frame f; CHECK(host_next(&parser,&f) && f.id==99 && f.cmd==100 && !f.size);
    host_parser_free(&parser); close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
}
static void acknowledgements(void) {
    kbd_t k; ctl_t c; init(&k,&c); FILE *out=tmpfile(); CHECK(out!=NULL); k.host_fd=fileno(out);
    for(int route=0;route<6;route++) { onboard_route(&k,(enum onboard_route)route); jpeg(&k); tile(&k,20,0,1); }
    fflush(out); rewind(out); uint8_t data[2048]; size_t n=fread(data,1,sizeof data,out);
    host_parser p; host_parser_init(&p,sizeof data); host_push(&p,data,n); host_frame f; int acks=0,errors=0;
    while(host_next(&p,&f)) { if(f.cmd==100) { CHECK(f.id==99 && f.size==0); acks++; } else { CHECK(f.id==77 && f.size==1 && f.data[0]=='E'); errors++; } }
    CHECK(acks==6 && errors==6); host_parser_free(&p); fclose(out); k.host_fd=-1; kbd_free(&k);
}
static void reservations(void) {
    ctl_t c; ctl_init(&c,NULL); ctl_set_allowed(&c,1); now=100;
    ctl_screen_grant(&c,1000,now);
    req(&c,"{\"op\":\"screen.text\",\"page_id\":0,\"fb\":1,\"text\":\"x\",\"size\":1,\"fg\":0,\"bg\":0}","reserved");
    req(&c,"{\"op\":\"screen.page.create\",\"name\":\"x\"}","page_id");
    for(int pid=1;pid<=3;pid++) {
        char r[512]; snprintf(r,sizeof r,"{\"op\":\"screen.bind\",\"page_id\":%d,\"row\":3,\"col\":4,\"action\":\"x\"}",pid); req(&c,r,"reserved");
        snprintf(r,sizeof r,"{\"op\":\"screen.draw\",\"page_id\":%d,\"fb\":21,\"x\":0,\"y\":111,\"w\":1,\"h\":2,\"fmt\":\"rgb565\",\"bytes\":4}",pid); req(&c,r,"reserved");
    }
    req(&c,"{\"op\":\"screen.bind\",\"page_id\":3,\"row\":3,\"col\":0,\"action\":\"x\"}","reserved");
    req(&c,"{\"op\":\"screen.text\",\"page_id\":3,\"fb\":16,\"text\":\"x\",\"size\":1,\"fg\":0,\"bg\":0}","reserved");
    CHECK(c.screen.page[3].bound==0 && c.screen.nwrites==0);
    req(&c,"{\"op\":\"screen.text\",\"page_id\":3,\"fb\":21,\"text\":\"body\",\"size\":16,\"fg\":65535,\"bg\":0}","rendered\":false");
    CHECK(c.screen.nwrites==1 && c.screen.page[3].bound==0);
    for(int pid=0;pid<=3;pid++) { ctl_set_allowed(&c,1); c.screen.current=pid; ctl_key_event(&c,3,4,1,now); CHECK(c.estopped && !c.allowed); }
}
static void legacy_and_ptt(void) {
    kbd_t k; ctl_t c; init(&k,&c);
    json(&k,"{\"method\":\"getInfo\"}"); CHECK(onboard_current(&k)==ONBOARD_HOME);
    onboard_route(&k,ONBOARD_L1);
    for(int i=0;i<3;i++) { jpeg(&k); finish_decode(&k); if(i<2) { CHECK(onboard_health(&k)==HOST_PROBING); now+=1000; } }
    CHECK(onboard_health(&k)==HOST_ONLINE && onboard_tile_allowed(&k,1,0,1));
    int p[2]; CHECK(!pipe(p)); fcntl(p[1],F_SETFL,O_NONBLOCK);
    hid_t h; hid_init(&h,"/nonexistent",NULL); hid_attach_fd(&h,p[1]); k.hid=&h;
    key(&k,16,1); CHECK(h.mods==HID_MOD_RALT);
    onboard_route(&k,ONBOARD_LS); CHECK(!h.mods); key(&k,16,0); CHECK(!h.mods);
    onboard_route(&k,ONBOARD_L5); key(&k,16,1); key(&k,16,0); CHECK(!h.mods);
    onboard_route(&k,ONBOARD_L1); now+=3000; kbd_safety_tick(&k,now);
    CHECK(onboard_health(&k)==HOST_SUSPECT); key(&k,5,1); CHECK(onboard_current(&k)==ONBOARD_L1);
    onboard_loss(&k); onboard_route(&k,ONBOARD_L1); key(&k,16,1); CHECK(!h.mods);
    jpeg(&k); pump(&k); CHECK(onboard_health(&k)==HOST_OFFLINE);
    json(&k,"{\"method\":\"getInfo\"}"); CHECK(onboard_health(&k)==HOST_PROBING);
    key(&k,20,1); CHECK(c.estopped && onboard_current(&k)==ONBOARD_HOME);
    k.hid=NULL; close(p[0]); close(p[1]); kbd_free(&k);
}
static void init_tick(void *ctx) { now+=100; kbd_service(ctx,now); }
static void initialization_deadline(void) {
    kbd_t k; ctl_t c; init(&k,&c); knob(&k,103,1);
    int sv[2]; CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,sv));
    gd32_parser parser; gd32_parser_init(&parser); gd32_knob_opts opts={20,1};
    CHECK(gd32_knob_init_service(sv[0],&parser,&opts,&k.knob,kbd_gd32_cb,&k,init_tick)==0);
    CHECK(c.estopped && onboard_current(&k)==ONBOARD_HOME); /* hold fires while echoes never arrive */
    unsigned char wire[512]; ssize_t n=read(sv[1],wire,sizeof wire); CHECK(n==6*13);
    CHECK(!gd32_raw_is_forbidden(wire,(size_t)n));
    close(sv[0]); close(sv[1]); kbd_free(&k);
}
static void pixels_and_regions(void) {
    kbd_t k; ctl_t c; init(&k,&c); uint16_t *px=malloc(428*142*2); CHECK(px!=NULL);
    for(int route=0;route<2;route++) {
        if(route) { key(&k,5,1); key(&k,5,0); now+=300; pump(&k); }
        for(int fb=1;fb<=20;fb++) { const panel_t *p=&PANELS[fb-1];
            CHECK(!ui_render_panel(onboard_ui(&k),fb,px,(size_t)p->w*p->h));
            CHECK(!memcmp(px,k.scr.pending[fb-1],(size_t)p->w*p->h*2));
        }
        for(int fb=1;fb<=21;fb++) { uint64_t before=hash(&k,fb); tile(&k,fb,0,1); CHECK(hash(&k,fb)==before); }
    }
    free(px); kbd_free(&k);
}
static void unread_receive_work(void) {
    kbd_t k; ctl_t c; init(&k,&c); hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k);
    int sv[2]; CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,sv)); k.host_fd=sv[0];
    fcntl(sv[0],F_SETFL,O_NONBLOCK); fcntl(sv[1],F_SETFL,O_NONBLOCK);
    int buffer_size=65536; setsockopt(sv[1],SOL_SOCKET,SO_SNDBUF,&buffer_size,sizeof buffer_size);
    setsockopt(sv[0],SOL_SOCKET,SO_RCVBUF,&buffer_size,sizeof buffer_size);
    size_t cap=jpg_size+24; uint8_t *wire=malloc(cap), *rx=malloc(cap); CHECK(wire && rx);
    CHECK(host_encode(456,100,jpg,jpg_size,wire,cap)==(int)cap);
    CHECK(write(sv[1],wire,cap)==(ssize_t)cap); /* complete frame queued in fd, outside parser */
    onboard_route(&k,ONBOARD_L1); CHECK(k.host_rx_barrier==cap);
    ssize_t n=read(sv[0],rx,cap); CHECK(n==(ssize_t)cap);
    if(n>0) kbd_host_feed(&k,rx,(size_t)n);
    pump(&k); CHECK(k.st.jpg_ok==0 && !onboard_tile_allowed(&k,1,0,1));
    unsigned char ack[64]; n=read(sv[1],ack,sizeof ack); CHECK(n==24);
    host_parser parser; host_parser_init(&parser,64); if(n>0) host_push(&parser,ack,(size_t)n);
    host_frame f; CHECK(host_next(&parser,&f) && f.id==456 && f.cmd==100 && !f.size);
    host_parser_free(&parser);
    jpeg(&k); finish_decode(&k); CHECK(k.st.jpg_ok==1 && onboard_tile_allowed(&k,1,0,1));
    /* One fresh post-switch frame must survive an older decode still in flight. */
    now+=300; jpeg(&k); onboard_route(&k,ONBOARD_L4); jpeg(&k);
    for(int i=0;i<1000 && !onboard_tile_allowed(&k,1,0,1);i++) { now++; kbd_service(&k,now); usleep(1000); }
    CHECK(onboard_tile_allowed(&k,1,0,1)); CHECK(onboard_current(&k)==ONBOARD_L4);
    key(&k,14,1); CHECK(onboard_current(&k)==ONBOARD_L4);
    key(&k,14,0); CHECK(onboard_current(&k)==ONBOARD_L1 && !onboard_tile_allowed(&k,1,0,1));
    free(wire); free(rx); close(sv[0]); close(sv[1]); k.host_fd=-1; kbd_free(&k);
}
static void in_flight_replacement(void) {
    kbd_t k; ctl_t c; init(&k,&c);
    char root[]="/tmp/mk20-u2-panels-XXXXXX", path[256]; CHECK(mkdtemp(root)!=NULL);
    for(int fb=1;fb<=21;fb++) {
        snprintf(path,sizeof path,"%s/fb%d",root,fb); int fd=open(path,O_CREAT|O_EXCL|O_RDWR,0600); CHECK(fd>=0); if(fd>=0) close(fd);
    }
    snprintf(k.scr.fbdir,sizeof k.scr.fbdir,"%s",root); screen_set_write_delay(&k.scr,10000);
    CHECK(!screen_start_writers(&k.scr));
    hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k);
    uint16_t *px=malloc(428*142*2); CHECK(px!=NULL);
    for(int route=0;route<3;route++) {
        onboard_route(&k,ONBOARD_L1); jpeg(&k); finish_decode(&k); /* host frames pending/in flight */
        CHECK(onboard_tile_allowed(&k,1,0,1));
        onboard_route(&k,(enum onboard_route)route); pump(&k);
        CHECK(!screen_wait_idle(&k.scr,3000)); /* replacements must follow any in-flight write */
        for(int fb=1;fb<=21;fb++) {
            const panel_t *p=&PANELS[fb-1]; size_t count=(size_t)p->w*p->h;
            snprintf(path,sizeof path,"%s/fb%d",root,fb); FILE *f=fopen(path,"rb"); CHECK(f!=NULL);
            if(f) { CHECK(fread(px,2,count,f)==count); fclose(f); CHECK(!memcmp(px,k.scr.pending[fb-1],count*2)); }
        }
    }
    free(px); kbd_free(&k);
    for(int fb=1;fb<=21;fb++) { snprintf(path,sizeof path,"%s/fb%d",root,fb); unlink(path); }
    CHECK(!rmdir(root));
}
#include "tests_onboard_r1.inc"
#include "tests_onboard_r2.inc"
#include "tests_onboard_r3.inc"
int main(void) {
    FILE *f=fopen("tests/data/canvas.jpg","rb"); if(!f) return 2;
    fseek(f,0,SEEK_END); jpg_size=(size_t)ftell(f); rewind(f); jpg=malloc(jpg_size); CHECK(fread(jpg,1,jpg_size,f)==jpg_size); fclose(f);
    offline_and_ownership(); health_and_stale(); safety(); acknowledgements(); reservations(); legacy_and_ptt(); initialization_deadline(); pixels_and_regions(); unread_receive_work(); in_flight_replacement(); round1_regressions(); round2_regressions(); round3_regressions(); free(jpg);
    printf("U2 onboard: %d checks, %d failures\n",checks,fails); return !!fails;
}
