/* 2026-10-20 on-device UX: status probes, read-only LS, boot auto-L1, host-owned fb20,
 * grace window, no auto-L5, host footer, pairing code and ui.openSettings.
 * Fake clock, temporary directories, pipes; no device paths are touched. */
#include "devstat.h"
static char ux_root[256];
static void ux_mkdirs(const char *rel)
{
    char p[512]; snprintf(p,sizeof p,"%s/%s",ux_root,rel);
    for (char *s=p+strlen(ux_root)+1;*s;s++) if (*s=='/') { *s=0; mkdir(p,0700); *s='/'; }
    mkdir(p,0700);
}
static void ux_put(const char *rel, const char *text, int mode)
{
    char p[512]; snprintf(p,sizeof p,"%s/%s",ux_root,rel);
    unlink(p); int fd=open(p,O_CREAT|O_EXCL|O_WRONLY,0600); CHECK(fd>=0);
    if (fd>=0) { CHECK(write(fd,text,strlen(text))==(ssize_t)strlen(text)); close(fd); }
    CHECK(!chmod(p,(mode_t)mode));
}
static void ux_rm(const char *rel) { char p[512]; snprintf(p,sizeof p,"%s/%s",ux_root,rel); unlink(p); }
static void ux_cleanup(void)
{
    static const char *const files[]={"tmp/mk20/gadget.env","sys/kernel/config/usb_gadget/g1/UDC","sys/class/udc/musb/state",
        "data/mk20/agent/net.json","data/mk20/agent/provider.json","data/mk20/agent/.apply-in-progress","data/mk20/agent/real.json",
        "data/mk20/secrets/glm-coding.key","data/mk20/secrets/qwen.key","data/mk20/secrets/real.key","run/mk20/pair-code","run/mk20/real-code","proc/uptime"};
    static const char *const dirs[]={"sys/class/net/wlan0","sys/class/net","sys/class/udc/musb","sys/class/udc","sys/class",
        "sys/kernel/config/usb_gadget/g1","sys/kernel/config/usb_gadget","sys/kernel/config","sys/kernel","sys","tmp/mk20","tmp",
        "data/mk20/agent","data/mk20/secrets","data/mk20","data","run/mk20","run","proc"};
    for (size_t i=0;i<sizeof files/sizeof *files;i++) ux_rm(files[i]);
    for (size_t i=0;i<sizeof dirs/sizeof *dirs;i++) { char p[512]; snprintf(p,sizeof p,"%s/%s",ux_root,dirs[i]); rmdir(p); }
    CHECK(!rmdir(ux_root));
}
static void ux_tree(void)
{
    char canonical[200]; CHECK(realpath("/tmp",canonical)!=NULL);
    snprintf(ux_root,sizeof ux_root,"%s/mk20-ux-XXXXXX",canonical); CHECK(mkdtemp(ux_root)!=NULL);
    static const char *const dirs[]={"tmp/mk20","sys/kernel/config/usb_gadget/g1","sys/class/udc/musb","sys/class/net",
        "data/mk20/agent","data/mk20/secrets","run/mk20","proc"};
    for (size_t i=0;i<sizeof dirs/sizeof *dirs;i++) ux_mkdirs(dirs[i]);
}
static void settle(kbd_t *k) { now+=2000; pump(k); }
/* Every cmd101 frame queued to the host: payloads copied out for exact comparison. */
static int ux_frames(int fd, char out[][192], uint32_t ids[], int max)
{
    uint8_t buf[16384]; host_parser p; host_parser_init(&p,sizeof buf);
    ssize_t n; while ((n=read(fd,buf,sizeof buf))>0) host_push(&p,buf,(size_t)n);
    host_frame f; int count=0;
    while (host_next(&p,&f)) if (f.cmd==HOST_CMD_JSON && count<max) {
        size_t len=f.size<191 ? f.size : 191; memcpy(out[count],f.data,len); out[count][len]=0; ids[count]=f.id; count++;
    }
    host_parser_free(&p); return count;
}
static int ux_count(char frames[][192], int n, const char *needle)
{ int c=0; for (int i=0;i<n;i++) c+=strstr(frames[i],needle)!=NULL; return c; }
static void ux_online_home(kbd_t *k) { hello(k); beat(k); now+=1000; beat(k); now+=1000; beat(k); pump(k); }
/* Keep a negotiated host ONLINE while time passes (heartbeats >= 1 s apart). */
static void live(kbd_t *k, int ms)
{
    for (int t=0;t<ms;t+=1000) { now+=1000; beat(k); for (int i=0;i<16;i++) kbd_service(k,now); }
    now+=1000; beat(k); for (int i=0;i<64;i++) kbd_service(k,now);
}

static void ux_status(void)
{
    kbd_t k; ctl_t c; init(&k,&c); ux_tree();
    const ui_status_t *s=&onboard_ui(&k)->st;
    /* Nothing present: calm defaults, never "unknown" placeholders. */
    onboard_status_root(&k,ux_root,NULL,NULL,NULL); settle(&k);
    CHECK(s->link==UI_LINK_UNSEEN && s->cfg==UI_CONF_DEFAULT && s->profile==-1 && s->provider==-1 && s->key==UI_KEYST_MISSING);
    CHECK(!strcmp(s->ip,"172.30.250.2") && s->ip_default && !s->wlan && !*s->fw && !strcmp(s->kbd,"test") && s->agent==UI_AGENT_OFF);
    ux_put("tmp/mk20/gadget.env","MK20_ECM_IP=172.30.250.9\nMK20_ECM_MASK=255.255.255.0\n",0600);
    ux_put("sys/kernel/config/usb_gadget/g1/UDC","musb\n",0644);
    ux_put("sys/class/udc/musb/state","configured\n",0444);
    ux_mkdirs("sys/class/net/wlan0");
    ux_put("data/mk20/agent/net.json","{\n \"version\": 1,\n \"profile\": \"board_wifi\",\n \"proxy\": {\n  \"url\": \"http://proxy.lan:7890\",\n  \"fallbacks\": []\n },\n \"routes\": {\n  \"domestic\": \"direct\",\n  \"foreign\": \"proxy\"\n }\n}",0600);
    ux_put("data/mk20/agent/provider.json","{\n \"version\": 1,\n \"provider\": \"glm-coding\",\n \"model\": \"glm-5.3-flash\",\n \"base_url\": \"https://open.bigmodel.cn/api/coding/paas/v4\",\n \"domestic\": true,\n \"route\": \"domestic\",\n \"api_key_file\": \"/data/mk20/secrets/glm-coding.key\"\n}",0600);
    ux_put("data/mk20/secrets/glm-coding.key","SECRET_SENTINEL_NEVER_RENDER",0600);
    onboard_status_root(&k,ux_root,NULL,"1.3.1",NULL); settle(&k);
    CHECK(!strcmp(s->ip,"172.30.250.9") && !s->ip_default && s->link==UI_LINK_NOAPP && s->wlan==1);
    CHECK(s->cfg==UI_CONF_OK && s->profile==1 && s->provider==1 && s->model==1 && s->key==UI_KEYST_SET);
    CHECK(!strcmp(s->fw,"1.3.1") && !strcmp(s->proxy,"http://proxy.lan:7890") && !strcmp(s->route_dom,"direct") && !strcmp(s->route_for,"proxy"));
    char url[64]; ui_web_url(onboard_ui(&k),url,sizeof url); CHECK(!strcmp(url,"http://172.30.250.9:8780"));
    /* Insecure/absent keys, symlinked and oversized configuration, markers, USB states. */
    char p[512], t[512];
    snprintf(p,sizeof p,"%s/data/mk20/secrets/glm-coding.key",ux_root); CHECK(!chmod(p,0644)); settle(&k); CHECK(s->key==UI_KEYST_INSECURE);
    CHECK(!chmod(p,0400)); settle(&k); CHECK(s->key==UI_KEYST_SET); /* same acceptance as net_read_key() */
    ux_rm("data/mk20/secrets/glm-coding.key"); settle(&k); CHECK(s->key==UI_KEYST_MISSING);
    ux_put("data/mk20/secrets/real.key","x",0600); snprintf(t,sizeof t,"%s/data/mk20/secrets/real.key",ux_root);
    CHECK(!symlink(t,p)); settle(&k); CHECK(s->key==UI_KEYST_INSECURE); ux_rm("data/mk20/secrets/glm-coding.key");
    snprintf(p,sizeof p,"%s/data/mk20/agent/net.json",ux_root); snprintf(t,sizeof t,"%s/data/mk20/agent/real.json",ux_root);
    CHECK(!rename(p,t)); CHECK(!symlink(t,p)); settle(&k); CHECK(s->cfg==UI_CONF_INVALID && s->profile==-2);
    ux_rm("data/mk20/agent/net.json"); settle(&k); CHECK(s->cfg==UI_CONF_INVALID && s->profile==-1); /* half a pair */
    ux_put("data/mk20/agent/net.json","{\"version\":1,\"profile\":\"mac_proxy\",\"proxy\":{\"url\":\"http://172.30.250.1:7890\",\"fallbacks\":[]},\"routes\":{\"domestic\":\"proxy\",\"foreign\":\"proxy\"}}",0600);
    settle(&k); CHECK(s->cfg==UI_CONF_OK && s->profile==0);
    ux_put("data/mk20/agent/.apply-in-progress","",0600); settle(&k); CHECK(s->cfg==UI_CONF_APPLYING);
    ux_rm("data/mk20/agent/.apply-in-progress");
    static char big[DS_FILE_MAX+64]; memset(big,' ',sizeof big-1); big[sizeof big-1]=0; memcpy(big,"{\"version\":1,\"provider\":\"qwen\",\"model\":\"qwen3-max\"}",51);
    ux_put("data/mk20/agent/provider.json",big,0600); settle(&k); CHECK(s->cfg==UI_CONF_INVALID && s->provider==-2 && s->key==UI_KEYST_UNKNOWN);
    ux_put("data/mk20/agent/provider.json","{\"version\":1,\"provider\":\"qwen\",\"model\":\"qwen3-max\",\"provider\":\"kimi\"}",0600);
    settle(&k); CHECK(s->provider==-2); /* duplicate key */
    ux_put("data/mk20/agent/provider.json","{\"version\":1,\"provider\":\"../x\",\"model\":\"qwen3-max\"}",0600); settle(&k); CHECK(s->provider==-2);
    ux_put("data/mk20/agent/provider.json","{\"version\":1,\"provider\":\"qwen\",\"model\":\"qwen3-max\"}",0600); settle(&k);
    CHECK(s->provider==0 && s->model==0 && s->key==UI_KEYST_MISSING);
    ux_put("sys/class/udc/musb/state","not attached\n",0444); settle(&k); CHECK(s->link==UI_LINK_USB_DOWN);
    ux_put("sys/kernel/config/usb_gadget/g1/UDC","\n",0644); settle(&k); CHECK(s->link==UI_LINK_USB_DOWN);
    ux_rm("tmp/mk20/gadget.env"); settle(&k); CHECK(!strcmp(s->ip,"172.30.250.2") && s->ip_default);
    /* Version fallback: MANIFEST of the active release. */
    devstat_t d; snprintf(p,sizeof p,"%s/tmp",ux_root); ux_put("tmp/MANIFEST","name=mk20\nversion=1.3.2-rc1\nfile=bin/mk20-kbd\n",0644);
    devstat_init(&d,ux_root,NULL,NULL,p); devstat_poll(&d,0,1); CHECK(!strcmp(d.r.version,"1.3.2-rc1"));
    devstat_init(&d,ux_root,NULL,"bad version!",p); devstat_poll(&d,0,1); CHECK(!strcmp(d.r.version,"1.3.2-rc1"));
    ux_rm("tmp/MANIFEST");
    /* One probe per call outside page entry; all due probes when kicked. */
    devstat_init(&d,ux_root,NULL,"9",NULL); devstat_poll(&d,0,0); CHECK(!*d.r.version);
    for (int i=0;i<DS_PROBES;i++) devstat_poll(&d,0,0);
    CHECK(!strcmp(d.r.version,"9"));
    /* LS is read-only: every key and knob leaves the configuration files untouched. */
    struct stat before, after; snprintf(p,sizeof p,"%s/data/mk20/agent/provider.json",ux_root); CHECK(!stat(p,&before));
    key(&k,5,1); key(&k,5,0); pump(&k); CHECK(onboard_current(&k)==ONBOARD_LS);
    for (int fb=1;fb<=19;fb++) {
        if (fb==16 || fb==18) continue;
        key(&k,fb,1); key(&k,fb,0); knob(&k,101,1); knob(&k,105,1); knob(&k,103,1); now+=20; knob(&k,103,0); pump(&k);
        CHECK(onboard_current(&k)==ONBOARD_LS && !c.estopped);
    }
    CHECK(!stat(p,&after) && after.st_mtime==before.st_mtime && after.st_size==before.st_size && after.st_ino==before.st_ino);
    snprintf(p,sizeof p,"%s/data/mk20/agent/.apply-in-progress",ux_root); CHECK(access(p,F_OK));
    key(&k,18,1); key(&k,18,0); CHECK(onboard_current(&k)==ONBOARD_HOME);
    kbd_free(&k); ux_cleanup();
}
static void ux_pair(void)
{
    char code[7];
    CHECK(!devstat_parse_pair("code=123456\nexpires_uptime=1300\n",1000,code) && !strcmp(code,"123456"));
    CHECK(devstat_parse_pair("code=12345\nexpires_uptime=1300\n",1000,code) && !*code);
    CHECK(devstat_parse_pair("code=1234567\nexpires_uptime=1300\n",1000,code));
    CHECK(devstat_parse_pair("code=12a456\nexpires_uptime=1300\n",1000,code));
    CHECK(devstat_parse_pair("code=123456\nexpires_uptime=1300",1000,code));
    CHECK(devstat_parse_pair("code=123456\nexpires_uptime=1300\nx\n",1000,code));
    CHECK(devstat_parse_pair("code=123456\r\nexpires_uptime=1300\n",1000,code));
    CHECK(devstat_parse_pair("code=123456\nexpires_uptime=1000\n",1000,code)); /* expired */
    CHECK(devstat_parse_pair("code=123456\nexpires_uptime=1601\n",1000,code)); /* > 10 min ahead */
    CHECK(!devstat_parse_pair("code=000000\nexpires_uptime=1600\n",1000,code) && !strcmp(code,"000000"));
    kbd_t k; ctl_t c; r2_online(&k,&c); ux_tree();
    onboard_status_root(&k,ux_root,NULL,NULL,NULL);
    ux_put("proc/uptime","1000.50 2000.00\n",0444);
    ux_put("run/mk20/pair-code","code=482916\nexpires_uptime=1300\n",0600);
    now+=250; pump(&k);
    const ui_t *u=onboard_ui(&k);
    CHECK(!strcmp(u->st.pair,"482916"));
    /* Live host page: the attention footer replaces only rows 112..141, and host
     * footer tiles are refused while it is shown. */
    CHECK(host_rows(&k,21,0,112) && !host_rows(&k,21,112,142));
    CHECK(onboard_tile_allowed(&k,21,0,1) && !onboard_tile_allowed(&k,21,111,2));
    /* Expiry (uptime passes it) removes the badge within one second; host footer returns. */
    ux_put("proc/uptime","1300.01 2000.00\n",0444); now+=1000; pump(&k); pump(&k);
    CHECK(!*u->st.pair && host_rows(&k,21,0,142) && onboard_tile_allowed(&k,21,111,2));
    /* Strict file: mode, symlink, size, content. */
    ux_put("proc/uptime","1000\n",0444);
    ux_put("run/mk20/pair-code","code=482916\nexpires_uptime=1300\n",0644); now+=1000; pump(&k); CHECK(!*u->st.pair);
    ux_put("run/mk20/pair-code","code=482916\nexpires_uptime=1300\n",0600); now+=1000; pump(&k); CHECK(!strcmp(u->st.pair,"482916"));
    char big[80]; memset(big,'1',sizeof big-1); big[sizeof big-1]=0; memcpy(big,"code=482916\nexpires_uptime=",27);
    ux_put("run/mk20/pair-code",big,0600); now+=1000; pump(&k); CHECK(!*u->st.pair);
    char p[512], t[512]; snprintf(p,sizeof p,"%s/run/mk20/pair-code",ux_root); snprintf(t,sizeof t,"%s/run/mk20/real-code",ux_root);
    ux_put("run/mk20/real-code","code=482916\nexpires_uptime=1300\n",0600); unlink(p); CHECK(!symlink(t,p));
    now+=1000; pump(&k); CHECK(!*u->st.pair);
    ux_rm("run/mk20/pair-code"); now+=1000; pump(&k); CHECK(!*u->st.pair);
    /* Environment override for the pairing path (tests/integration). */
    setenv("MK20_KBD_PAIR_CODE",t,1); setenv("MK20_ROOT",ux_root,1);
    devstat_t d; devstat_init_env(&d); CHECK(!strcmp(d.pair_path,t) && !strcmp(d.root,ux_root));
    devstat_poll(&d,0,0); CHECK(!strcmp(d.r.pair,"482916"));
    unsetenv("MK20_KBD_PAIR_CODE"); setenv("MK20_ROOT","/nonexistent-u2-status",1);
    /* Every page shows it: HOME and LS through ui.c, L5 through the compositor footer. */
    ux_put("run/mk20/pair-code","code=482916\nexpires_uptime=1300\n",0600);
    onboard_route(&k,ONBOARD_L5); now+=1000; pump(&k); CHECK(ui_footer_attention(onboard_ui(&k)));
    uint16_t with[428*30]; memcpy(with,k.scr.pending[20]+428*112,sizeof with);
    ux_rm("run/mk20/pair-code"); now+=1000; pump(&k); CHECK(memcmp(with,k.scr.pending[20]+428*112,sizeof with));
    kbd_free(&k); ux_cleanup();
}
static void ux_fb20(void)
{
    char frames[64][192]; uint32_t ids[64];
    /* Agent inactive on L1: fb20 is the Mac mode key (host face, host key event). */
    kbd_t k; ctl_t c; r2_online(&k,&c);
    int p[2]; CHECK(!pipe(p)); CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK)); k.host_fd=p[1];
    CHECK(host_rows(&k,20,0,128) && onboard_tile_allowed(&k,20,0,1));
    key(&k,20,1); key(&k,20,0);
    int n=ux_frames(p[0],frames,ids,64); char down[160], up[160];
    host_json_key_state(down,sizeof down,4,3,1); host_json_key_state(up,sizeof up,4,3,0);
    CHECK(n==2 && !strcmp(frames[0],down) && !strcmp(frames[1],up) && ids[0]==KBD_EVENT_ID);
    CHECK(!c.estopped && onboard_current(&k)==ONBOARD_L1);
    /* Agent allowed: e-stop, no host event, stay on L1 with a stop banner. */
    ctl_set_allowed(&c,1); pump(&k); CHECK(!host_rows(&k,20,0,128) && !onboard_tile_allowed(&k,20,0,1));
    uint16_t estop_face[128*128]; memcpy(estop_face,k.scr.pending[19],sizeof estop_face);
    key(&k,20,1); CHECK(c.estopped && !c.allowed && onboard_current(&k)==ONBOARD_L1 && c.screen.current==2);
    key(&k,20,0); n=ux_frames(p[0],frames,ids,64); CHECK(ux_count(frames,n,"keyStateChanged")==0);
    live(&k,0);
    CHECK(onboard_ui(&k)->stop_banner && !host_rows(&k,21,112,142) && host_rows(&k,21,0,112));
    CHECK(host_rows(&k,20,0,128)); /* agent now disabled: the host's mode key is back */
    live(&k,10000); CHECK(onboard_health(&k)==HOST_ONLINE && !onboard_ui(&k)->stop_banner && host_rows(&k,21,0,142));
    /* Lease held (allowed implied): e-stop. K2 hold: e-stop, also on L1. */
    ctl_set_allowed(&c,1); req(&c,"{\"op\":\"lease.acquire\",\"ttl_ms\":3000}","token"); CHECK(c.token);
    key(&k,20,1); CHECK(c.estopped && !c.token && onboard_current(&k)==ONBOARD_L1); key(&k,20,0);
    ctl_set_allowed(&c,1); live(&k,0);
    knob(&k,103,1); now+=1000; kbd_service(&k,now); CHECK(c.estopped && onboard_current(&k)==ONBOARD_L1); knob(&k,103,0);
    CHECK(onboard_current(&k)==ONBOARD_L1); live(&k,0);
    /* K2 hold with the agent inactive still latches the stop and stays. */
    knob(&k,103,1); now+=1000; kbd_service(&k,now); CHECK(c.estopped && onboard_current(&k)==ONBOARD_L1); knob(&k,103,0);
    live(&k,0); CHECK(onboard_health(&k)==HOST_ONLINE);
    /* Held Mac PTT is released by an e-stop that stays on the page. */
    int hp[2]; CHECK(!pipe(hp)); CHECK(!fcntl(hp[1],F_SETFL,O_NONBLOCK));
    hid_t h; hid_init(&h,"/nonexistent",NULL); hid_attach_fd(&h,hp[1]); k.hid=c.hid=&h;
    live(&k,0); key(&k,16,1); CHECK(h.mods==HID_MOD_RALT);
    live(&k,0); CHECK(onboard_ui(&k)->ptt_held && !host_rows(&k,21,112,142)); /* PTT footer */
    knob(&k,103,1); now+=1000; kbd_service(&k,now); CHECK(c.estopped && !h.mods); knob(&k,103,0);
    key(&k,16,0); CHECK(!h.mods);
    k.hid=c.hid=NULL; close(hp[0]); close(hp[1]);
    /* Every on-board page keeps fb20 = e-stop with the agent inactive. */
    enum onboard_route pages[]={ONBOARD_HOME,ONBOARD_LS,ONBOARD_L5};
    for (int i=0;i<3;i++) {
        ctl_set_allowed(&c,0); c.estopped=0; onboard_route(&k,pages[i]); pump(&k); CHECK(!c.estopped);
        key(&k,20,1); key(&k,20,0); CHECK(c.estopped);
    }
    ux_frames(p[0],frames,ids,64); close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
}
static void ux_grace(void)
{
    char frames[64][192]; uint32_t ids[64];
    for (int variant=0; variant<3; variant++) {
        kbd_t k; ctl_t c; r2_online(&k,&c);
        int p[2]; CHECK(!pipe(p)); CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK)); k.host_fd=p[1];
        knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4);
        int n=ux_frames(p[0],frames,ids,64); CHECK(n==2); /* the K2 mode tap only */
        static const int keys[]={5,6,7,18};
        for (int i=0;i<4;i++) { key(&k,keys[i],1); key(&k,keys[i],0); }
        n=ux_frames(p[0],frames,ids,64);
        CHECK(onboard_current(&k)==ONBOARD_L4 && !n); /* swallowed: no navigation, no host event */
        if (variant==0) {
            /* A fresh frame ends the grace window: keys belong to the host again. */
            now+=300; jpeg(&k); finish_decode(&k); CHECK(onboard_tile_allowed(&k,1,0,1));
            key(&k,5,1); key(&k,5,0); n=ux_frames(p[0],frames,ids,64);
            CHECK(onboard_current(&k)==ONBOARD_L4 && ux_count(frames,n,"keyStateChanged")==2);
        } else if (variant==1) {
            /* No frame for 2 s: the placeholder's navigation labels and actions return. */
            for (int i=0;i<4;i++) { now+=500; beat(&k); pump(&k); }
            key(&k,5,1); key(&k,5,0); CHECK(onboard_current(&k)==ONBOARD_LS);
        } else {
            /* Host offline (placeholder): navigation works at once, as labelled. */
            onboard_loss(&k); key(&k,6,1); key(&k,6,0); CHECK(onboard_current(&k)==ONBOARD_HOME);
        }
        close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
    }
}
static void ux_auto_l1(void)
{
    kbd_t k; ctl_t c;
    init_boot(&k,&c); CHECK(onboard_current(&k)==ONBOARD_HOME);
    ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_L1 && c.screen.current==2 && onboard_health(&k)==HOST_ONLINE);
    /* Only the first time: after user navigation and a host loss/reconnect, stay put. */
    knob(&k,100,1); now+=1000; kbd_service(&k,now); knob(&k,100,0); CHECK(onboard_current(&k)==ONBOARD_HOME);
    onboard_loss(&k); ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_HOME);
    kbd_free(&k);
    /* Any key or knob before ONLINE disarms it. */
    init_boot(&k,&c); key(&k,1,1); key(&k,1,0); ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_HOME); kbd_free(&k);
    init_boot(&k,&c); knob(&k,105,1); ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_HOME); kbd_free(&k);
    /* Not from another page (e.g. an agent-shown L5), and consumed by that first ONLINE. */
    init_boot(&k,&c); onboard_route(&k,ONBOARD_L5); ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_L5);
    onboard_route(&k,ONBOARD_HOME); onboard_loss(&k); ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_HOME); kbd_free(&k);
    /* E-stop then reconnect never navigates either. */
    init_boot(&k,&c); ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_L1);
    ctl_set_allowed(&c,1); key(&k,20,1); key(&k,20,0); CHECK(c.estopped && onboard_current(&k)==ONBOARD_L1);
    onboard_route(&k,ONBOARD_HOME); onboard_loss(&k); ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_HOME); kbd_free(&k);
    /* Legacy host (getInfo + full frames only) on the boot HOME. */
    init_boot(&k,&c); json(&k,"{\"method\":\"getInfo\"}");
    for (int i=0;i<3 && onboard_current(&k)==ONBOARD_HOME;i++) { jpeg(&k); finish_decode(&k); if (i<2) now+=1000; }
    CHECK(onboard_current(&k)==ONBOARD_L1 && onboard_health(&k)==HOST_ONLINE && !onboard_tile_allowed(&k,1,0,1));
    now+=300; jpeg(&k); finish_decode(&k); CHECK(onboard_tile_allowed(&k,1,0,1)); /* fresh frame after the switch */
    kbd_free(&k);
}
static void ux_confirm(void)
{
    kbd_t k; ctl_t c; r2_online(&k,&c); ctl_set_allowed(&c,1); pump(&k);
    int hp[2]; CHECK(!pipe(hp)); CHECK(!fcntl(hp[1],F_SETFL,O_NONBLOCK));
    hid_t h; hid_init(&h,"/nonexistent",NULL); hid_attach_fd(&h,hp[1]); k.hid=&h;
    key(&k,16,1); CHECK(h.mods==HID_MOD_RALT);
    req(&c,"{\"op\":\"confirm.request\",\"cid\":9,\"risk\":\"low\",\"summary\":\"ux\",\"timeout_ms\":5000}","true");
    now+=250; pump(&k);
    /* No auto-L5: the page, its keys and the held Mac PTT are untouched; footer badge only. */
    CHECK(onboard_current(&k)==ONBOARD_L1 && h.mods==HID_MOD_RALT && onboard_ui(&k)->st.confirm);
    CHECK(!host_rows(&k,21,112,142) && host_rows(&k,21,0,112));
    key(&k,16,0); CHECK(!h.mods);
    onboard_route(&k,ONBOARD_HOME); pump(&k); CHECK(onboard_ui(&k)->st.confirm);
    key(&k,6,1); key(&k,6,0); CHECK(onboard_current(&k)==ONBOARD_L5 && c.confirm.pending);
    /* Deadline unchanged: it still times out while nobody confirms. */
    now+=5000; kbd_service(&k,now); CHECK(!c.confirm.pending); pump(&k); CHECK(!onboard_ui(&k)->st.confirm);
    k.hid=NULL; close(hp[0]); close(hp[1]); kbd_free(&k);
}
static void ux_open_settings(void)
{
    char frames[64][192]; uint32_t ids[64]; static const char want[]="{\"method\":\"ui.openSettings\"}";
    kbd_t k; ctl_t c; init(&k,&c);
    int p[2]; CHECK(!pipe(p)); CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK)); k.host_fd=p[1];
    /* Host not ONLINE: nothing is sent, the toast tells the user the URL. */
    key(&k,19,1); key(&k,19,0); int n=ux_frames(p[0],frames,ids,64);
    CHECK(!n && onboard_ui(&k)->toast==UI_SUB_WEB_OFFLINE && onboard_current(&k)==ONBOARD_HOME);
    ux_online_home(&k); CHECK(onboard_current(&k)==ONBOARD_HOME && onboard_health(&k)==HOST_ONLINE);
    ux_frames(p[0],frames,ids,64); /* hello/heartbeat acks */
    key(&k,19,1); key(&k,19,0); n=ux_frames(p[0],frames,ids,64);
    CHECK(n==1 && !strcmp(frames[0],want) && ids[0]==KBD_EVENT_ID && onboard_ui(&k)->toast==UI_SUB_WEB_OPENED);
    now+=1000; beat(&k); now+=1000; beat(&k); now+=999; beat(&k); ux_frames(p[0],frames,ids,64);
    key(&k,19,1); key(&k,19,0); n=ux_frames(p[0],frames,ids,64); CHECK(!n && onboard_ui(&k)->toast==UI_SUB_WEB_OPENED); /* rate limit */
    now+=1; key(&k,19,1); key(&k,19,0); n=ux_frames(p[0],frames,ids,64); CHECK(n==1 && !strcmp(frames[0],want));
    /* Other HOME keys never emit it; the read-only LS key does (same limit). */
    for (int fb=1;fb<=18;fb++) if (fb!=5 && fb!=6 && fb!=7 && fb!=8 && fb!=15) { key(&k,fb,1); key(&k,fb,0); }
    n=ux_frames(p[0],frames,ids,64); CHECK(!ux_count(frames,n,"openSettings"));
    now+=1000; beat(&k); key(&k,5,1); key(&k,5,0); CHECK(onboard_current(&k)==ONBOARD_LS);
    now+=2000; beat(&k); ux_frames(p[0],frames,ids,64);
    key(&k,19,1); key(&k,19,0); n=ux_frames(p[0],frames,ids,64); CHECK(ux_count(frames,n,"openSettings")==1);
    /* On a live host page fb19 is an ordinary host key, never openSettings. */
    kbd_free(&k); close(p[0]); close(p[1]);
    r2_online(&k,&c); CHECK(!pipe(p)); CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK)); k.host_fd=p[1];
    key(&k,19,1); key(&k,19,0); n=ux_frames(p[0],frames,ids,64);
    CHECK(!ux_count(frames,n,"openSettings") && ux_count(frames,n,"keyStateChanged")==2);
    close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
}
static void round4_regressions(void)
{
    ux_status(); ux_pair(); ux_fb20(); ux_grace(); ux_auto_l1(); ux_confirm(); ux_open_settings();
}
