/* Offline simulator bridge: fake clock, no writer threads, sockets, HID or tty.
 * Commands: time <ms> | key <row> <col> <down> | host <json> | jpeg <file> | loss |
 *           allow <0|1> | root <dir> [version] | state | canvas (P6 640x656 on stdout). */
#include "kbd.h"
#include "ctl.h"
#include "onboard.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
static int64_t now;
static int64_t clock_ms(void *ctx) { (void)ctx; return now; }
static void pump(kbd_t *k) { for (int i=0;i<24;i++) kbd_service(k,now); }
int main(void)
{
    kbd_t k; ctl_t c; char line[512];
    if (kbd_init(&k,"/nonexistent-onboard-headless","","headless") || onboard_enable(&k)) return 1;
    k.clock_ms=clock_ms; ctl_init(&c,NULL); k.ctl=&c;
    k.k2_estop=1; k2_estop_init(&k.k2,1);
    /* Deterministic status: no device files unless a test root is given. */
    onboard_status_root(&k,"/nonexistent-onboard-headless-root",NULL,NULL,NULL);
    const ui_t *u=onboard_ui(&k);
    while (fgets(line,sizeof line,stdin)) {
        int row,col,down,v; long long ms; char dir[256], ver[24];
        if (sscanf(line,"key %d %d %d",&row,&col,&down)==3) {
            if (row<0 || row>105 || col<0 || col>105) return 2;
            uint8_t ev[]={0x16,(uint8_t)down,(uint8_t)row,(uint8_t)col,0,0}; kbd_on_gd32(&k,ev,6);
        } else if (sscanf(line,"time %lld",&ms)==1 && ms>=now) now=ms;
        else if (sscanf(line,"allow %d",&v)==1) ctl_set_allowed(&c,v);
        else if (sscanf(line,"root %255s %23s",dir,ver)>=1) {
            int n=sscanf(line,"root %255s %23s",dir,ver);
            onboard_status_root(&k,dir,NULL,n==2 ? ver : NULL,NULL);
        } else if (sscanf(line,"jpeg %255s",dir)==1) {
            /* Host cmd100 full frame from a file; waits for the real decoder thread. */
            FILE *f=fopen(dir,"rb"); static uint8_t data[1<<20]; size_t n=f ? fread(data,1,sizeof data,f) : 0;
            if (f) fclose(f);
            if (!n) return 2;
            unsigned before=k.st.jpg_ok+k.st.jpg_bad+k.st.jpg_size;
            host_frame fr={2,HOST_CMD_JPG,(uint32_t)n,data}; kbd_on_host_frame(&k,&fr);
            for (int i=0;i<2000 && before==k.st.jpg_ok+k.st.jpg_bad+k.st.jpg_size;i++) { kbd_service(&k,now); usleep(1000); }
        } else if (!strncmp(line,"host ",5)) {
            const char *m=line+5; host_frame f={1,HOST_CMD_JSON,(uint32_t)strcspn(m,"\n"),(const uint8_t *)m}; kbd_on_host_frame(&k,&f);
        } else if (!strcmp(line,"loss\n")) onboard_loss(&k);
        else if (!strcmp(line,"canvas\n")) {
            pump(&k); printf("P6\n%d %d\n255\n",CANVAS_W,CANVAS_H);
            for (int y=0;y<CANVAS_H;y++) for (int x=0;x<CANVAS_W;x++) {
                uint16_t px=0;
                for (int i=0;i<PANEL_COUNT;i++) { const panel_t *p=&PANELS[i]; if (x>=p->x && x<p->x+p->w && y>=p->y && y<p->y+p->h) { px=k.scr.pending[i][(y-p->y)*p->w+x-p->x]; break; } }
                unsigned char rgb[]={(unsigned char)((px>>11)*255/31),(unsigned char)(((px>>5)&63)*255/63),(unsigned char)((px&31)*255/31)};
                if (fwrite(rgb,1,3,stdout)!=3) return 1;
            }
            fflush(stdout); continue;
        } else if (strcmp(line,"state\n")) return 2;
        pump(&k);
        static const char *const routes[]={"HOME","LS","L5","L1","L4","AGENT"};
        static const char *const health[]={"offline","probing","online","suspect"};
        static const char *const links[]={"unseen","usb_down","usb_sleep","noapp","probing","online","stale"};
        static const char *const cfgs[]={"default","ok","invalid","applying"};
        static const char *const keys[]={"unknown","missing","set","insecure"};
        const ui_status_t *s=&u->st; int pi=s->provider>=0 ? s->provider : 0, mi=s->model>=0 ? s->model : 0;
        printf("{\"page\":\"%s\",\"host_health\":\"%s\",\"stopped\":%s,\"allowed\":%s,\"ctl_page\":%d,\"pair\":\"%s\",\"confirm\":%s,"
               "\"settings\":{\"active\":%s,\"tab\":%d,\"focus\":%d,\"link\":\"%s\",\"cfg\":\"%s\",\"key\":\"%s\",\"profile\":\"%s\",\"provider\":\"%s\",\"model\":\"%s\",\"fw\":\"%s\"},\"toast\":\"%s\"}\n",
            routes[onboard_current(&k)],health[onboard_health(&k)],c.estopped?"true":"false",c.allowed?"true":"false",c.screen.current,s->pair,s->confirm?"true":"false",
            u->route==UI_LS?"true":"false",u->tab,u->focus,links[s->link],cfgs[s->cfg],keys[s->key],
            s->profile==1?"board_wifi":s->profile==-2?"invalid":"mac_proxy",s->provider==-2?"invalid":UI_PROVIDERS[pi].id,
            s->provider==-2?"invalid":UI_PROVIDERS[pi].models[mi].id,s->fw,UI_LABELS[u->toast]);
        fflush(stdout);
    }
    kbd_free(&k); return 0;
}
