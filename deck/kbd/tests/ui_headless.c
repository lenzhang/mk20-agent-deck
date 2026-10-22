#define _DARWIN_C_SOURCE
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "ui.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
static void ppm(const char *path,const uint16_t *p,int w,int h)
{
    FILE *f=fopen(path,"wb"); if (!f) { perror("ppm"); exit(1); }
    fprintf(f,"P6\n%d %d\n255\n",w,h);
    for (int i=0;i<w*h;i++) { unsigned char rgb[3]={(unsigned char)(((p[i]>>11)&31)*255/31),(unsigned char)(((p[i]>>5)&63)*255/63),(unsigned char)((p[i]&31)*255/31)};
        if (fwrite(rgb,1,3,f)!=3) exit(1); }
    if (fclose(f)) exit(1);
}
static void online(ui_t *u)
{
    u->host_online=1; u->st.link=UI_LINK_ONLINE; u->st.ip_default=0;
    strcpy(u->st.fw,"1.3.1"); strcpy(u->st.kbd,"mk20-kbd-0.3");
}
static void scene(ui_t *u,int i)
{
    ui_init(u);
    switch (i) {
    case 0: return;
    case 1: online(u); return;
    case 2: online(u); u->st.cfg=UI_CONF_OK; u->st.profile=1; strcpy(u->st.proxy,"http://proxy.lan:7890");
        strcpy(u->st.route_dom,"direct"); strcpy(u->st.route_for,"proxy"); u->st.provider=1; u->st.model=1;
        u->st.key=UI_KEYST_SET; u->st.wlan=1; u->st.agent=UI_AGENT_ALLOWED; u->st.task=3; return;
    case 3: u->st.link=UI_LINK_USB_DOWN; return;
    case 4: online(u); u->st.agent=UI_AGENT_LEASED; u->st.task=4; u->st.confirm=1; return;
    case 5: u->st.link=UI_LINK_NOAPP; ui_key(u,19); return;
    case 6: online(u); ui_key(u,19); return;
    case 7: u->st.link=UI_LINK_NOAPP; strcpy(u->st.pair,"482916"); return;
    case 8: online(u); ui_key(u,5); return;
    case 9: online(u); u->st.provider=1; u->st.model=1; u->st.cfg=UI_CONF_OK; ui_key(u,5); ui_key(u,2); ui_key(u,7); return;
    case 10: u->st.key=UI_KEYST_INSECURE; ui_key(u,5); ui_key(u,3); return;
    case 11: ui_key(u,5); ui_key(u,4); return;
    case 12: online(u); ui_key(u,15); return;
    case 13: u->st.agent=UI_AGENT_STOPPED; ui_key(u,20); return;
    case 14: u->st.cfg=UI_CONF_INVALID; u->st.profile=-2; u->st.provider=-2; u->st.model=-2; u->st.key=UI_KEYST_UNKNOWN; return;
    case 15: online(u); ui_key(u,5); ui_key(u,17); return; /* 1.6.0 LS fb17 会话安排: ui.openArrange sent */
    case 16: u->st.link=UI_LINK_NOAPP; ui_key(u,5); ui_key(u,17); return; /* ... Mac not ONLINE: nothing sent */
    }
}
int main(int argc,char **argv)
{
    static const char *names[]={"home-offline","home-online","home-configured","home-usb-down","home-confirm","home-web-offline","home-web-opened","home-pair","ls-network","ls-model","ls-key","ls-test","ls-info","stopped","home-invalid","ls-arrange-opened","ls-arrange-offline"};
    if (argc!=2) return 2;
    uint16_t *p=malloc(428*142*2),*canvas=malloc(CANVAS_W*CANVAS_H*2); if (!p||!canvas) return 1;
    for (size_t s=0;s<sizeof names/sizeof names[0];s++) {
        ui_t u; scene(&u,(int)s); char path[1024],dir[900]; snprintf(dir,sizeof dir,"%s/%s",argv[1],names[s]);
        if (mkdir(dir,0700)) return 1;
        for (int i=0;i<CANVAS_W*CANVAS_H;i++) canvas[i]=(uint16_t)(((14>>3)<<11)|((16>>2)<<5)|(20>>3));
        for (int fb=1;fb<=PANEL_COUNT;fb++) {
            const panel_t *b=&PANELS[fb-1]; if (ui_render_panel(&u,fb,p,(size_t)b->w*b->h)) return 1;
            snprintf(path,sizeof path,"%s/fb%02d.ppm",dir,fb); ppm(path,p,b->w,b->h);
            for (int y=0;y<b->h;y++) memcpy(canvas+(b->y+y)*CANVAS_W+b->x,p+y*b->w,(size_t)b->w*2);
        }
        snprintf(path,sizeof path,"%s/canvas.ppm",dir); ppm(path,canvas,CANVAS_W,CANVAS_H);
    }
    free(p); free(canvas); printf("ui headless: %zu scenes, %zu P6 PPMs\n",sizeof names/sizeof names[0],sizeof names/sizeof names[0]*22); return 0;
}
