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
static void scene(ui_t *u,int i)
{
    ui_init(u,NULL,NULL,NULL);
    if (i==0) return;
    if (i==1) { u->host_online=1; u->key_present=1; return; }
    ui_key(u,5);
    if (i<=6) { ui_key(u,i-1); if (i==3) ui_key(u,7); if (i==4) { u->draft.provider=1; ui_key(u,7); } return; }
    if (i==7) { ui_key(u,7); ui_key(u,17); }
    if (i==8) { ui_key(u,7); ui_key(u,16); }
    if (i==9) { ui_key(u,4); ui_key(u,6); }
    if (i==10) { ui_key(u,4); u->key_present=1; u->injected_result=UI_RESULT_AUTH; u->latency_ms=180; ui_key(u,6); }
    if (i==11) { ui_key(u,8); strcpy(u->edit,"a-very-long-explicit-proxy-hostname.example.invalid"); u->cursor=20; }
    if (i==12) { ui_key(u,20); }
}
int main(int argc,char **argv)
{
    static const char *names[]={"home-offline","home-online","network","provider-draft","model-vision","test","info","network-confirm","discard-confirm","missing-key","test-auth","host-editor","stopped"};
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
    free(p); free(canvas); puts("ui headless: 13 scenes, 286 P6 PPMs"); return 0;
}
