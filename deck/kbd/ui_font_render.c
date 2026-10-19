#include "ui_font.h"
#include <string.h>
uint32_t ui_font_next(const char **s)
{
    const unsigned char *p=(const unsigned char *)*s; uint32_t cp; int n;
    if (!*p) return 0;
    if (*p<128) { (*s)++; return *p; }
    if (*p>=0xc2 && *p<=0xdf) { n=2; cp=*p&31; }
    else if (*p>=0xe0 && *p<=0xef) { n=3; cp=*p&15; }
    else if (*p>=0xf0 && *p<=0xf4) { n=4; cp=*p&7; }
    else goto invalid;
    for (int i=1;i<n;i++) { if ((p[i]&0xc0)!=0x80) goto invalid; cp=(cp<<6)|(p[i]&63); }
    if ((n==3 && cp<0x800)||(n==4 && cp<0x10000)||cp>0x10ffff||(cp>=0xd800&&cp<=0xdfff)) goto invalid;
    *s+=n; return cp;
invalid: (*s)++; return 0xfffd;
}
static const ui_glyph_t *lookup(uint32_t cp)
{
    size_t lo=0, hi=UI_GLYPH_COUNT;
    while (lo<hi) { size_t m=lo+(hi-lo)/2; if (UI_GLYPHS[m].codepoint<cp) lo=m+1; else hi=m; }
    return lo<UI_GLYPH_COUNT && UI_GLYPHS[lo].codepoint==cp ? &UI_GLYPHS[lo] : NULL;
}
int ui_font_has(uint32_t cp) { return lookup(cp)!=NULL; }
int ui_font_coverage(const char *s)
{ while (*s) if (!ui_font_has(ui_font_next(&s))) return 0; return 1; }
void ui_font_draw(uint16_t *p,int w,int h,int x,int y,const char *s,uint16_t c,int max_width)
{
    int used=0;
    if (!p || w<=0 || h<=0 || max_width<=0) return;
    while (*s) {
        const ui_glyph_t *g=lookup(ui_font_next(&s)); if (!g) g=lookup(0xfffd);
        if (!g) return;
        const char *rest=s; int remaining=0;
        while (*rest && remaining<=max_width) { const ui_glyph_t *r=lookup(ui_font_next(&rest)); remaining+=r?r->width:16; }
        int truncated=used+g->width+remaining>max_width;
        if (truncated && used+g->width+16>max_width) { g=lookup(0x2026); s=""; }
        if (used+g->width>max_width) return;
        for (int yy=0;yy<16;yy++) for (int xx=0;xx<g->width;xx++) {
            int px=x+used+xx,py=y+yy;
            if (px>=0&&py>=0&&px<w&&py<h&&(g->rows[yy]&(0x8000u>>xx))) p[py*w+px]=c;
        }
        used+=g->width;
    }
}
