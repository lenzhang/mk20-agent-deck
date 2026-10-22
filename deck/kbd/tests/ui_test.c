#define _DARWIN_C_SOURCE
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "ui.h"
#include "ui_font.h"
#include <assert.h>
#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static unsigned checks;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); abort(); } } while (0)
static void state_tests(void)
{
    ui_t u; ui_init(&u); CHECK(u.route==UI_HOME && u.toast==UI_NONE && u.st.link==UI_LINK_UNSEEN);
    ui_key(&u,5); CHECK(u.route==UI_LS && u.tab==UI_NET && u.focus==0);
    for (int t=0;t<UI_TAB_COUNT;t++) { ui_key(&u,t+1); CHECK((int)u.tab==t && u.focus==0); }
    ui_key(&u,1); ui_key(&u,8); CHECK(u.focus==2);
    ui_key(&u,15); CHECK(u.focus==2); /* network shows eight items: fb14/fb15 are blank */
    ui_knob(&u,2,UI_CW,0); CHECK(u.focus==3);
    for (int n=0;n<4;n++) ui_knob(&u,2,UI_CCW,0);
    CHECK(u.focus==ui_item_count(UI_NET)-1);
    ui_knob(&u,1,UI_CW,0); CHECK(u.tab==UI_MODELS && u.focus==0);
    ui_knob(&u,1,UI_CCW,0); ui_knob(&u,1,UI_CCW,0); CHECK(u.tab==UI_DEVICE);
    ui_key(&u,17); CHECK(u.route==UI_LS && u.toast==UI_SUB_ARRANGE_OFFLINE); /* 1.6.0 会话安排 (was the inert Apply) */
    u.host_online=1; ui_key(&u,17); CHECK(u.route==UI_LS && u.toast==UI_SUB_ARRANGE_OPENED); u.host_online=0;
    ui_key(&u,1); CHECK(u.toast==UI_NONE && u.tab==UI_NET); ui_knob(&u,1,UI_CCW,0); CHECK(u.tab==UI_DEVICE);
    ui_key(&u,16); CHECK(u.route==UI_HOME && !u.back_home);
    ui_key(&u,15); CHECK(u.route==UI_LS && u.tab==UI_DEVICE);
    ui_key(&u,18); CHECK(u.route==UI_HOME && u.back_home);
    u.host_online=0; ui_key(&u,19); CHECK(u.toast==UI_SUB_WEB_OFFLINE && u.route==UI_HOME);
    u.host_online=1; ui_key(&u,19); CHECK(u.toast==UI_SUB_WEB_OPENED);
    ui_key(&u,1); CHECK(u.toast==UI_NONE);
    ui_key(&u,5); u.host_online=0; ui_key(&u,19); CHECK(u.toast==UI_SUB_WEB_OFFLINE && u.route==UI_LS);
    ui_knob(&u,1,UI_PRESS,0); CHECK(u.toast==UI_SUB_SUMMARY); ui_knob(&u,1,UI_PRESS,0); CHECK(u.toast==UI_NONE);
    ui_knob(&u,1,UI_DOWN,5000); ui_tick(&u,5999); CHECK(u.route==UI_LS); ui_tick(&u,6000); CHECK(u.route==UI_HOME && u.back_home);
    ui_knob(&u,1,UI_UP,6001); CHECK(u.toast!=UI_SUB_SUMMARY);
    ui_knob(&u,2,UI_DOWN,7000); ui_tick(&u,7999); CHECK(!u.stopped); ui_tick(&u,8000); CHECK(u.stopped); ui_knob(&u,2,UI_UP,8001); CHECK(u.route==UI_HOME);
    ui_init(&u); ui_key(&u,20); CHECK(u.stopped);
    for (int t=0;t<5;t++) { ui_init(&u); ui_key(&u,5); ui_key(&u,t+1); ui_key(&u,20); CHECK(u.stopped && u.route==UI_LS); }
    ui_init(&u); ui_knob(&u,2,UI_CCW,0); CHECK(u.home_focus==18); ui_knob(&u,2,UI_PRESS,0); CHECK(u.toast==UI_SUB_WEB_OFFLINE);
    ui_init(&u); for (int n=0;n<4;n++) ui_knob(&u,2,UI_CW,0);
    CHECK(u.home_focus==4); ui_knob(&u,2,UI_PRESS,0); CHECK(u.route==UI_LS && u.tab==UI_NET);
    /* Read-only: no key/knob sequence can change the configuration snapshot. */
    unsigned seed=1; ui_init(&u); u.st.provider=1; u.st.model=1; u.st.profile=1; ui_status_t want; memcpy(&want,&u.st,sizeof want);
    for (int n=0;n<20000;n++) {
        seed=seed*1103515245u+12345u; int r=(int)((seed>>16)%26);
        if (r<20) ui_key(&u,r+1); else ui_knob(&u,1+(r&1),(enum ui_action)((r-20)%5),n);
        ui_tick(&u,n);
        CHECK(!memcmp(&want,&u.st,sizeof want)); CHECK((int)u.tab>=0 && u.tab<UI_TAB_COUNT && u.focus>=0 && u.focus<ui_item_count(u.tab));
    }
}
static void footer_tests(void)
{
    ui_t u; ui_init(&u);
    for (int i=0;i<UI_LABEL_COUNT;i++) CHECK(!strstr(UI_LABELS[i],"unknown"));
    CHECK(!*UI_LABELS[UI_NONE]);
    for (int c=UI_CTX_HOME;c<=UI_CTX_AGENT;c++) { u.ctx=(enum ui_context)c; CHECK(!ui_footer_attention(&u)); }
    strcpy(u.st.pair,"123456");
    for (int c=UI_CTX_HOME;c<=UI_CTX_AGENT;c++) { u.ctx=(enum ui_context)c; CHECK(ui_footer_attention(&u)); }
    u.st.pair[0]=0; u.stop_banner=1;
    u.ctx=UI_CTX_HOST; CHECK(ui_footer_attention(&u)); u.ctx=UI_CTX_HOME; CHECK(!ui_footer_attention(&u));
    u.stop_banner=0; u.st.confirm=1;
    u.ctx=UI_CTX_HOST; CHECK(ui_footer_attention(&u)); u.ctx=UI_CTX_AGENT; CHECK(ui_footer_attention(&u));
    u.ctx=UI_CTX_L5; CHECK(!ui_footer_attention(&u)); u.ctx=UI_CTX_HOME; CHECK(!ui_footer_attention(&u));
    u.st.confirm=0; u.health_stale=1; u.ctx=UI_CTX_HOST; CHECK(ui_footer_attention(&u));
    u.health_stale=0; u.ptt_held=1; CHECK(ui_footer_attention(&u)); u.ctx=UI_CTX_LS; CHECK(!ui_footer_attention(&u));
    u.ptt_held=0; u.incompatible=1; CHECK(ui_footer_attention(&u)); u.incompatible=0;
    /* Persistent agent e-stop notice: every page, and it replaces the 10 s banner. */
    u.stop_notice=1; for (int c=UI_CTX_HOME;c<=UI_CTX_AGENT;c++) { u.ctx=(enum ui_context)c; CHECK(ui_footer_attention(&u)); }
    u.stop_notice=0; u.ctx=UI_CTX_LS;
    CHECK(ui_font_coverage("配对码 ") && ui_font_coverage(UI_LABELS[UI_PAIR])); /* safe-mode rescue text */
    char url[64]; ui_web_url(&u,url,sizeof url); CHECK(!strcmp(url,"http://172.30.250.2:8780"));
    strcpy(u.st.ip,"10.1.2.3"); ui_web_url(&u,url,sizeof url); CHECK(!strcmp(url,"http://10.1.2.3:8780"));
    /* The footer band differs with and without a pairing code; the body never changes. */
    uint16_t *a=malloc(428*142*2), *b=malloc(428*142*2); CHECK(a && b);
    u.ctx=UI_CTX_HOME; CHECK(!ui_render_panel(&u,21,a,428*142));
    strcpy(u.st.pair,"654321"); CHECK(!ui_render_panel(&u,21,b,428*142));
    CHECK(!memcmp(a,b,428*112*2) && memcmp(a+428*112,b+428*112,428*30*2));
    /* Missing status never renders as an alarm: a default HOME and a configured one differ. */
    ui_init(&u); CHECK(!ui_render_panel(&u,14,a,128*128));
    u.st.key=UI_KEYST_SET; CHECK(!ui_render_panel(&u,14,b,128*128)); CHECK(memcmp(a,b,128*128*2));
    /* A stale status snapshot keeps the value but renders it differently (dim + 读取超时). */
    u.st.key=UI_KEYST_SET; CHECK(!ui_render_panel(&u,14,a,128*128)); u.st.stale=1; CHECK(!ui_render_panel(&u,14,b,128*128));
    CHECK(memcmp(a,b,128*128*2)); u.st.stale=0;
    free(a); free(b);
}
static void font_tests(void)
{
    for (int i=0;i<UI_LABEL_COUNT;i++) CHECK(ui_font_coverage(UI_LABELS[i]));
    for (size_t i=0;i<UI_PROVIDER_COUNT;i++) {
        CHECK(ui_font_coverage(UI_PROVIDERS[i].label)); CHECK(ui_font_coverage(UI_PROVIDERS[i].id)); CHECK(ui_font_coverage(UI_PROVIDERS[i].base_url));
        for (size_t j=0;j<UI_PROVIDERS[i].model_count;j++) CHECK(ui_font_coverage(UI_PROVIDERS[i].models[j].id));
    }
    CHECK(ui_font_has(0x27f2) && ui_font_has(0x27f3) && ui_font_has(0x2713) && ui_font_has(0x2026) && ui_font_has(0x23f9) && ui_font_has(0x1f399));
    CHECK(!ui_font_coverage("\xf4\x8f\xbf\xbf"));
    const char *bad="\xe0"; CHECK(ui_font_next(&bad)==0xfffd && !*bad);
    uint16_t buf[260]={0}; buf[0]=0x1234; buf[259]=0x5678;
    ui_font_draw(buf+1,16,16,0,0,"\xf4\x8f\xbf\xbf",0xffff,16);
    int n=0; for (int i=1;i<=256;i++) n+=buf[i]!=0; CHECK(n>0 && buf[0]==0x1234 && buf[259]==0x5678);
    ui_font_draw(buf+1,16,16,-2,-2,"abcdefghijklmnopqrstuvwxyz",3,100); CHECK(buf[0]==0x1234 && buf[259]==0x5678);
    ui_t u; ui_init(&u); uint16_t *pixels=malloc(428*142*2+4); CHECK(pixels!=NULL);
    for (int route=0;route<2;route++) for (int t=0;t<5;t++) {
        u.route=(enum ui_route)route; u.tab=(enum ui_tab)t;
        for (int fb=1;fb<=21;fb++) {
            size_t count=(size_t)PANELS[fb-1].w*PANELS[fb-1].h; pixels[count]=0xbeef;
            CHECK(ui_render_panel(&u,fb,pixels,count)==0); CHECK(pixels[count]==0xbeef);
            CHECK(ui_render_panel(&u,fb,pixels,count-1)==-1);
        }
    }
    free(pixels);
}
typedef struct { int at, seen, mode; } faults_t;
static ssize_t fault(void *ctx, enum uicfg_op op, size_t n)
{
    faults_t *f=ctx;
    if (op==UICFG_PARENT_FSYNC_SYSCALL) return (ssize_t)n;
    f->seen++;
    if (f->mode==2 && op==UICFG_WRITE) return 0;
    if (f->mode==1 && op==UICFG_WRITE) { if (f->seen%2) { errno=EINTR; return -1; } return n>1?1:(ssize_t)n; }
    if (f->seen==f->at) { errno=op==UICFG_WRITE?ENOSPC:EIO; return -1; }
    return (ssize_t)n;
}
static void mode(const char *path,int wanted) { struct stat st; CHECK(!lstat(path,&st)); CHECK((st.st_mode&0777)==(mode_t)wanted); }
static void content(const char *path,const char *expected)
{
    char b[8192]; FILE *f=fopen(path,"rb"); CHECK(f!=NULL); size_t n=fread(b,1,sizeof b-1,f); b[n]=0; fclose(f);
    CHECK(!strcmp(b,expected)); CHECK(!strstr(b,"SECRET_SENTINEL_NEVER_RENDER"));
}
static int path_components(const char *path)
{
    int n=0;
    for (const char *p=path;*p;p++) if (*p!='/' && (p==path || p[-1]=='/')) n++;
    return n;
}
typedef struct { int at, dirs, other; const char *new_dir; } dir_faults_t;
static ssize_t dir_fault(void *ctx, enum uicfg_op op, size_t n)
{
    dir_faults_t *f=ctx;
    if (op==UICFG_DIR_FSYNC) {
        f->dirs++;
        if (f->dirs==f->at || (f->new_dir && !access(f->new_dir,F_OK))) { errno=EIO; return -1; }
    } else if (op!=UICFG_PARENT_FSYNC_SYSCALL) f->other++;
    return (ssize_t)n;
}
static void writer_parent_fsync_retry_tests(void)
{
    char canonical[512], temp[1024], parent[1200], root[1400], path[1600];
    CHECK(realpath("/tmp",canonical)!=NULL);
    snprintf(temp,sizeof temp,"%s/mk20-ui-retry-XXXXXX",canonical); CHECK(mkdtemp(temp)!=NULL);
    snprintf(parent,sizeof parent,"%s/nested",temp); snprintf(root,sizeof root,"%s/config",parent);
    uicfg_t c; uicfg_defaults(&c);
    /* The final walk sync is nested/'s fsync after mkdirat("config"). */
    int component=path_components(root);
    dir_faults_t f={0,0,0,root}; uicfg_hooks_t hooks={dir_fault,&f};
    CHECK(uicfg_apply(root,&c,&hooks)==-1); CHECK(errno==EIO);
    CHECK(f.dirs>0 && f.other==0); mode(root,0700);
    snprintf(path,sizeof path,"%s/.apply-lock",root); CHECK(access(path,F_OK)==-1 && errno==ENOENT);

    /* Keep the directories: every existing component must still sync its parent. */
    f=(dir_faults_t){0,0,0,NULL}; CHECK(!uicfg_apply(root,&c,&hooks));
    CHECK(f.dirs==component+4); /* Walk plus marker, two renames, marker removal. */
    f=(dir_faults_t){component,0,0,NULL};
    CHECK(uicfg_apply(root,&c,&hooks)==-1); CHECK(errno==EIO);
    CHECK(f.dirs==component && f.other==0); mode(root,0700);

    const char *files[]={"net.json","provider.json",".apply-lock"};
    for (size_t i=0;i<sizeof files/sizeof files[0];i++) {
        snprintf(path,sizeof path,"%s/%s",root,files[i]); CHECK(!unlink(path));
    }
    CHECK(!rmdir(root)); CHECK(!rmdir(parent)); CHECK(!rmdir(temp));
}
typedef struct { enum uicfg_op op; int at, seen, error; } parent_sync_fault_t;
static ssize_t parent_sync_fault(void *ctx, enum uicfg_op op, size_t n)
{
    parent_sync_fault_t *f=ctx;
    if (op==f->op && ++f->seen==f->at) { errno=f->error; return -1; }
    return (ssize_t)n;
}
static void writer_parent_fsync_errno_tests(void)
{
    char canonical[512], temp[1024], root[1200], path[1400];
    const int errors[]={EINVAL,EROFS,EIO};
    const enum uicfg_op ops[]={UICFG_PARENT_FSYNC_SYSCALL,UICFG_DIR_FSYNC};
    const char *files[]={"net.json","provider.json",".apply-lock"};
    uicfg_t c; uicfg_defaults(&c);
    CHECK(realpath("/tmp",canonical)!=NULL);
    snprintf(temp,sizeof temp,"%s/mk20-ui-errno-XXXXXX",canonical); CHECK(mkdtemp(temp)!=NULL);
    snprintf(root,sizeof root,"%s/config",temp);
    for (size_t op=0;op<sizeof ops/sizeof ops[0];op++)
        for (size_t e=0;e<sizeof errors/sizeof errors[0];e++)
            for (int created=0;created<2;created++) {
                if (!created) CHECK(!mkdir(root,0700));
                parent_sync_fault_t f={ops[op],path_components(root),0,errors[e]};
                uicfg_hooks_t hooks={parent_sync_fault,&f};
                int succeeds=ops[op]==UICFG_PARENT_FSYNC_SYSCALL && !created && errors[e]!=EIO;
                int rc=uicfg_apply(root,&c,&hooks);
                CHECK(rc==(succeeds?0:-1));
                if (!succeeds) {
                    CHECK(errno==errors[e]); CHECK(f.seen==f.at);
                    snprintf(path,sizeof path,"%s/.apply-lock",root);
                    CHECK(access(path,F_OK)==-1 && errno==ENOENT);
                } else {
                    for (size_t i=0;i<sizeof files/sizeof files[0];i++) {
                        snprintf(path,sizeof path,"%s/%s",root,files[i]); CHECK(!unlink(path));
                    }
                }
                CHECK(!rmdir(root));
            }
    CHECK(!rmdir(temp));
}
static void writer_tests(void)
{
    char temp[1024], canonical[512], root[1200],path[1600], net[4096],prov[4096];
    CHECK(realpath("/tmp",canonical)!=NULL); snprintf(temp,sizeof temp,"%s/mk20-ui-XXXXXX",canonical); CHECK(mkdtemp(temp)!=NULL); uicfg_t c; uicfg_defaults(&c);
    snprintf(root,sizeof root,"%s/config",temp); CHECK(!uicfg_apply(root,&c,NULL)); mode(root,0700);
    CHECK(!uicfg_serialize(&c,net,sizeof net,prov,sizeof prov));
    snprintf(path,sizeof path,"%s/net.json",root); mode(path,0600); content(path,net);
    snprintf(path,sizeof path,"%s/provider.json",root); mode(path,0600); content(path,prov);
    snprintf(path,sizeof path,"%s/.apply-in-progress",root); CHECK(access(path,F_OK)!=0);
    faults_t f={0,0,1}; uicfg_hooks_t hooks={fault,&f}; CHECK(!uicfg_apply(root,&c,&hooks)); CHECK(f.seen>100);
    f=(faults_t){0,0,0}; CHECK(!uicfg_apply(root,&c,&hooks)); int ops=f.seen; CHECK(ops>=10);
    for (int at=1;at<=ops;at++) {
        CHECK(!uicfg_apply(root,&c,NULL)); f=(faults_t){at,0,0}; c.model=1;
        CHECK(uicfg_apply(root,&c,&hooks)==-1);
        snprintf(path,sizeof path,"%s/.apply-in-progress",root);
        if (at<=path_components(root)) CHECK(access(path,F_OK)==-1 && errno==ENOENT);
        else mode(path,0600);
        DIR *d=opendir(root); CHECK(d!=NULL); struct dirent *entry;
        while ((entry=readdir(d))) CHECK(strstr(entry->d_name,".tmp.")==NULL);
        closedir(d); c.model=0; CHECK(!uicfg_apply(root,&c,NULL));
    }
    f=(faults_t){0,0,2}; CHECK(uicfg_apply(root,&c,&hooks)==-1); CHECK(!uicfg_apply(root,&c,NULL));
    snprintf(path,sizeof path,"%s/.net.json.tmp.%ld.0",root,(long)getpid());
    CHECK(!symlink("/nonexistent",path)); CHECK(!uicfg_apply(root,&c,NULL));
    struct stat tempst; CHECK(!lstat(path,&tempst) && S_ISLNK(tempst.st_mode)); CHECK(!unlink(path));
    snprintf(path,sizeof path,"%s/.apply-lock",root); CHECK(!unlink(path)); CHECK(!symlink("/nonexistent",path));
    CHECK(uicfg_apply(root,&c,NULL)==-1); CHECK(!unlink(path)); CHECK(!uicfg_apply(root,&c,NULL));
    snprintf(path,sizeof path,"%s/provider.json",root); CHECK(!unlink(path)); CHECK(!symlink("/nonexistent",path)); CHECK(uicfg_apply(root,&c,NULL)==-1); CHECK(!unlink(path));
    snprintf(path,sizeof path,"%s/.apply-in-progress",root); CHECK(!symlink("/nonexistent",path)); CHECK(uicfg_apply(root,&c,NULL)==-1); CHECK(!unlink(path));
    snprintf(path,sizeof path,"%s/link",temp); CHECK(!symlink(root,path)); CHECK(uicfg_apply(path,&c,NULL)==-1);
    char child[1700]; snprintf(child,sizeof child,"%s/child",path); CHECK(uicfg_apply(child,&c,NULL)==-1);
    strcpy(c.proxy_host,"bad/host"); CHECK(!uicfg_valid(&c)); CHECK(uicfg_apply(root,&c,NULL)==-1);
    uicfg_defaults(&c);
    const char *badhosts[]={"-bad.example","bad..example","999.1.1.1","01.2.3.4","bad-.example"};
    for (size_t i=0;i<sizeof badhosts/sizeof badhosts[0];i++) { strcpy(c.proxy_host,badhosts[i]); CHECK(!uicfg_valid(&c)); }
    uicfg_defaults(&c); strcpy(c.wifi_proxy,"http://user:pass@host:123"); CHECK(!uicfg_valid(&c));
    char quoted[128]; CHECK(!uicfg_quote("中文\n\t\"\\\001",quoted,sizeof quoted)); CHECK(!strcmp(quoted,"\"中文\\n\\t\\\"\\\\\\u0001\""));
    CHECK(uicfg_quote("long",quoted,3)==-1);
    /* Delete only our known temp tree, never shell out or access a secret path. */
    CHECK(!unlink(path)); DIR *d=opendir(root); CHECK(d!=NULL); struct dirent *e;
    while ((e=readdir(d))) if (strcmp(e->d_name,".") && strcmp(e->d_name,"..")) { snprintf(path,sizeof path,"%s/%s",root,e->d_name); CHECK(!unlink(path)); }
    closedir(d); CHECK(!rmdir(root)); CHECK(!rmdir(temp));
}
int main(void) { state_tests(); footer_tests(); font_tests(); writer_parent_fsync_retry_tests(); writer_parent_fsync_errno_tests(); writer_tests(); printf("ui: %u checks passed (ASan/UBSan)\n",checks); return 0; }
