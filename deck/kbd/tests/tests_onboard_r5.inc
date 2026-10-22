/* 2026-10-21 new L1 layout (user decision): on a live L1/L4 page fb17/fb18/fb19 are the Mac's
 * Backspace/Return/Escape (USB HID, held while held, never host key events), gated like the Mac PTT.
 * Fake clock, nonblocking pipes and FIFOs in a temporary directory; no device paths are touched. */
#define L1_HOLD_MS 10000 /* onboard.c HID_KEY_HOLD_MS */
static const int l1_fb[3]={17,18,19}, l1_usage[3]={0x2A,0x28,0x29};
/* Every 9-byte keyboard report written to the fake hidg0 since the last call. */
static int l1_reports(int fd, uint8_t rep[][9], int max)
{
    uint8_t buf[1024]; size_t have=0; ssize_t r; int n=0;
    while (have<sizeof buf && (r=read(fd,buf+have,sizeof buf-have))>0) have+=(size_t)r;
    CHECK(have%9==0);
    for (size_t i=0;i+9<=have && n<max;i+=9) { CHECK(buf[i]==1); memcpy(rep[n++],buf+i,9); }
    return n;
}
static int l1_is(const uint8_t *r, int mod, int k0, int k1, int k2)
{ uint8_t w[9]={1,(uint8_t)mod,0,(uint8_t)k0,(uint8_t)k1,(uint8_t)k2,0,0,0}; return !memcmp(r,w,9); }
/* Exactly one new report, equal to the given state. */
static int l1_one(int fd, int mod, int k0, int k1, int k2)
{ uint8_t r[16][9]; int n=l1_reports(fd,r,16); return n==1 && l1_is(r[0],mod,k0,k1,k2); }
static int l1_none(int fd) { uint8_t r[16][9]; return l1_reports(fd,r,16)==0; }
static int l1_host_keys(int fd) { char f[64][192]; uint32_t ids[64]; int n=ux_frames(fd,f,ids,64); return ux_count(f,n,"keyStateChanged"); }
static void l1_setup(kbd_t *k, ctl_t *c, hid_t *h, int hp[2], int p[2])
{
    r2_online(k,c);
    CHECK(!pipe(p)); CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK)); k->host_fd=p[1];
    CHECK(!pipe(hp)); CHECK(!fcntl(hp[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(hp[1],F_SETFL,O_NONBLOCK));
    hid_init(h,"/nonexistent",NULL); hid_attach_fd(h,hp[1]); k->hid=c->hid=h;
    CHECK(l1_one(hp[0],0,0,0,0)); /* startup all-up */
}
static void l1_teardown(kbd_t *k, ctl_t *c, int hp[2], int p[2])
{ k->hid=c->hid=NULL; close(hp[0]); close(hp[1]); close(p[0]); close(p[1]); k->host_fd=-1; kbd_free(k); }
/* Fresh full frame after a host-page switch (ends the grace window). */
static void l1_fresh(kbd_t *k) { now+=300; jpeg(k); finish_decode(k); CHECK(onboard_tile_allowed(k,1,0,1)); }

static void l1_keys_reports(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    for (int page=0;page<2;page++) {
        for (int i=0;i<3;i++) {
            key(&k,l1_fb[i],1); CHECK(l1_one(hp[0],0,l1_usage[i],0,0));
            key(&k,l1_fb[i],0); CHECK(l1_one(hp[0],0,0,0,0));
        }
        CHECK(!l1_host_keys(p[0])); /* consumed like the PTT: never keyStateChanged */
        CHECK(onboard_current(&k)==(page ? ONBOARD_L4 : ONBOARD_L1));
        if (!page) { knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); l1_fresh(&k); l1_host_keys(p[0]); }
    }
    /* Ordinary keys stay host events without HID; fb16 is still the PTT. */
    key(&k,12,1); key(&k,12,0); CHECK(l1_host_keys(p[0])==2 && l1_none(hp[0]));
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0));
    /* A nonzero GD32 keycode (incompatible mapping) never synthesizes a second HID key. */
    uint8_t ev[]={0x16,1,3,1,0,0x2A}; kbd_on_gd32(&k,ev,6); ev[1]=0; kbd_on_gd32(&k,ev,6);
    CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    /* --no-hid: still consumed (no host key event), nothing written anywhere. */
    k.hid=c.hid=NULL; key(&k,18,1); key(&k,18,0); CHECK(!l1_host_keys(p[0])); k.hid=c.hid=&h; CHECK(l1_none(hp[0]));
    l1_teardown(&k,&c,hp,p);
}
static void l1_hold(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0));
    live(&k,5000); CHECK(l1_none(hp[0])); /* held 6 s: no synthetic repeats, macOS repeats a held key */
    key(&k,17,1); CHECK(l1_none(hp[0])); /* repeated down (lost GD32 up): still one down */
    key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    key(&k,17,0); CHECK(l1_none(hp[0])); /* duplicate up */
    /* Lost key-up guard: a hold is cut after L1_HOLD_MS; the late physical up is a no-op. */
    key(&k,18,1); CHECK(l1_one(hp[0],0,0x28,0,0));
    pump(&k); int t=onboard_timeout(&k,now,60000); CHECK(t>0 && t<=L1_HOLD_MS-250); /* loop wakes for the cut */
    live(&k,8000); CHECK(l1_none(hp[0])); /* 9 s */
    live(&k,0); CHECK(l1_one(hp[0],0,0,0,0)); /* 10 s */
    key(&k,18,0); CHECK(l1_none(hp[0]));
    key(&k,18,1); CHECK(l1_one(hp[0],0,0x28,0,0)); key(&k,18,0); CHECK(l1_one(hp[0],0,0,0,0)); /* press again: works */
    /* The cut never touches the PTT (a long dictation hold) or another held key's own deadline. */
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); live(&k,4000);
    key(&k,19,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0x29,0,0)); live(&k,8000);
    CHECK(l1_none(hp[0]) && h.mods==HID_MOD_RALT); /* 14 s PTT, 9 s Escape */
    live(&k,0); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0));
    key(&k,19,0); key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0));
    CHECK(!l1_host_keys(p[0]));
    l1_teardown(&k,&c,hp,p);
}
static void l1_inert_pages(void)
{
    char frames[64][192]; uint32_t ids[64];
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    /* HOME keeps its meaning: fb19 sends ui.openSettings (host ONLINE), no HID. */
    onboard_route(&k,ONBOARD_HOME); pump(&k); ux_frames(p[0],frames,ids,64);
    for (int i=0;i<3;i++) { key(&k,l1_fb[i],1); key(&k,l1_fb[i],0); }
    int n=ux_frames(p[0],frames,ids,64);
    CHECK(onboard_current(&k)==ONBOARD_HOME && ux_count(frames,n,"openSettings")==1 && !ux_count(frames,n,"keyStateChanged"));
    CHECK(l1_none(hp[0]));
    /* LS: fb17/19 read-only, fb18 主页. */
    onboard_route(&k,ONBOARD_LS); pump(&k);
    key(&k,17,1); key(&k,17,0); CHECK(onboard_current(&k)==ONBOARD_LS);
    key(&k,18,1); key(&k,18,0); CHECK(onboard_current(&k)==ONBOARD_HOME && l1_none(hp[0]));
    /* L5: no HID. */
    onboard_route(&k,ONBOARD_L5); pump(&k);
    for (int i=0;i<3;i++) { key(&k,l1_fb[i],1); key(&k,l1_fb[i],0); }
    CHECK(onboard_current(&k)==ONBOARD_L5 && l1_none(hp[0]));
    /* Agent page binding fb17-19: the agent gets the key events, the Mac gets no HID. */
    int a[2]; CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,a)); CHECK(!fcntl(a[1],F_SETFL,O_NONBLOCK));
    c.cfd[0]=a[0]; ctl_set_allowed(&c,1);
    c.screen.page[3]=(ctl_page_t){.used=1,.id=3,.client=0,.bound=(1u<<16)|(1u<<17)|(1u<<18)};
    c.screen.current=3; kbd_safety_tick(&k,now); CHECK(onboard_current(&k)==ONBOARD_AGENT);
    char buf[2048];
    for (int i=0;i<3;i++) {
        key(&k,l1_fb[i],1); key(&k,l1_fb[i],0);
        ssize_t m=read(a[1],buf,sizeof buf-1); buf[m>0?m:0]=0; char want[48]; snprintf(want,sizeof want,"\"row\":3,\"col\":%d",l1_fb[i]-16);
        CHECK(m>0 && strstr(buf,want) && strstr(buf,"\"down\":true") && strstr(buf,"\"down\":false"));
    }
    CHECK(l1_none(hp[0]) && !c.estopped);
    ctl_close(&c); close(a[1]);
    l1_teardown(&k,&c,hp,p);
}
static void l1_inert_states(void)
{
    char frames[64][192]; uint32_t ids[64];
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    /* Grace window (ONLINE page switch, no fresh frame yet): swallowed, no HID, no host event. */
    knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); ux_frames(p[0],frames,ids,64);
    for (int i=0;i<3;i++) { key(&k,l1_fb[i],1); key(&k,l1_fb[i],0); }
    CHECK(onboard_current(&k)==ONBOARD_L4 && l1_none(hp[0]) && !ux_frames(p[0],frames,ids,64));
    l1_fresh(&k); key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0)); key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    /* SUSPECT (3 s silence): frozen. */
    now+=3000; kbd_safety_tick(&k,now); CHECK(onboard_health(&k)==HOST_SUSPECT);
    for (int i=0;i<3;i++) { key(&k,l1_fb[i],1); key(&k,l1_fb[i],0); }
    CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    /* Offline placeholder: fb17/fb19 inert, fb18 is its labelled Home key. */
    onboard_loss(&k); pump(&k);
    key(&k,17,1); key(&k,17,0); key(&k,19,1); key(&k,19,0); CHECK(onboard_current(&k)==ONBOARD_L4 && l1_none(hp[0]));
    key(&k,18,1); key(&k,18,0); CHECK(onboard_current(&k)==ONBOARD_HOME && l1_none(hp[0]));
    /* PROBING (handshake, not yet ONLINE). */
    onboard_route(&k,ONBOARD_L1); hello(&k); beat(&k); CHECK(onboard_health(&k)==HOST_PROBING);
    CHECK(l1_host_keys(p[0])==2); /* 1.6.0: the hello announces L4 -> L1 (changed while offline) with the K2 tap */
    key(&k,17,1); key(&k,17,0); key(&k,19,1); key(&k,19,0); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    l1_teardown(&k,&c,hp,p);
    /* Post-e-stop suspension: swallowed until everything is up and 1 s is quiet. */
    l1_setup(&k,&c,&h,hp,p);
    knob(&k,103,1); now+=1000; kbd_service(&k,now); CHECK(c.estopped); knob(&k,103,0); uint8_t r[16][9]; l1_reports(hp[0],r,16);
    key(&k,17,1); key(&k,17,0); key(&k,18,1); key(&k,18,0); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    key(&k,19,1); live(&k,1000); key(&k,19,0); CHECK(l1_none(hp[0])); /* held through the pause: its up is swallowed too */
    live(&k,0); key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0)); key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    l1_teardown(&k,&c,hp,p);
}
/* PTT + fb17 held, then each release path: no key may stay down on the Mac. */
static void l1_release_paths(void)
{
    for (int mode=0;mode<9;mode++) {
        kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
        if (mode==0) { ctl_set_allowed(&c,1); live(&k,0); } /* agent active: fb20 is the e-stop */
        key(&k,17,1); key(&k,16,1); key(&k,19,1);
        uint8_t r[16][9]; int n=l1_reports(hp[0],r,16);
        CHECK(n==3 && l1_is(r[0],0,0x2A,0,0) && l1_is(r[1],HID_MOD_RALT,0x2A,0,0) && l1_is(r[2],HID_MOD_RALT,0x2A,0x29,0));
        int64_t t0=real_ms();
        switch (mode) {
        case 0: key(&k,20,1); CHECK(c.estopped && real_ms()-t0 < 50); key(&k,20,0); break;   /* fb20 e-stop */
        case 1: knob(&k,103,1); now+=1000; kbd_service(&k,now); CHECK(c.estopped); knob(&k,103,0); break; /* K2 hold */
        case 2: req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped); break; /* socket */
        case 3: knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); break; /* K2 short */
        case 4: json(&k,"{\"method\":\"ui.page\",\"page\":\"L4\",\"generation\":9}"); CHECK(onboard_current(&k)==ONBOARD_L4); break;
        case 5: knob(&k,100,1); now+=1000; kbd_service(&k,now); knob(&k,100,0); CHECK(onboard_current(&k)==ONBOARD_HOME); break;
        case 6: onboard_loss(&k); CHECK(onboard_health(&k)==HOST_OFFLINE); break;          /* host loss / EOF */
        case 7: now+=3000; kbd_safety_tick(&k,now); CHECK(onboard_health(&k)==HOST_SUSPECT); break;
        case 8: json(&k,"{\"method\":\"ui.hello\",\"version\":1}"); break;               /* new handshake */
        }
        n=l1_reports(hp[0],r,16);
        CHECK(n>=1 && n<=4 && l1_is(r[n-1],0,0,0,0) && !h.mods && !h.keys[0] && !h.keys[1] && !h.keys[2]);
        if (mode==3 || mode==4) CHECK(n==3 && l1_is(r[0],0,0x2A,0x29,0) && l1_is(r[1],0,0,0x29,0)); /* one up each, in order */
        key(&k,17,0); key(&k,16,0); key(&k,19,0); CHECK(l1_none(hp[0])); /* late physical ups: nothing */
        l1_teardown(&k,&c,hp,p);
    }
}
/* Real hidg0 lifetime (FIFOs): node replacement, gadget teardown stop file and the exit path. */
static void l1_hid_lifetime(void)
{
    kbd_t k; ctl_t c; r2_online(&k,&c);
    char root[]="/tmp/mk20-l1hid-XXXXXX", path[128], old[128], old2[128], stop_path[128];
    CHECK(mkdtemp(root)!=NULL);
    snprintf(path,sizeof path,"%s/hid",root); snprintf(old,sizeof old,"%s/old",root);
    snprintf(old2,sizeof old2,"%s/old2",root); snprintf(stop_path,sizeof stop_path,"%s/stop",root);
    CHECK(!mkfifo(path,0600)); int hr=open(path,O_RDONLY|O_NONBLOCK); CHECK(hr>=0);
    hid_t h; hid_init(&h,path,stop_path); k.hid=c.hid=&h; kbd_service(&k,now); CHECK(l1_one(hr,0,0,0,0));
    /* Held across a replacement: the new node gets all-up, then the still-held key (like a held RAlt). */
    key(&k,17,1); CHECK(l1_one(hr,0,0x2A,0,0));
    CHECK(!rename(path,old)); CHECK(!mkfifo(path,0600)); int hr2=open(path,O_RDONLY|O_NONBLOCK); CHECK(hr2>=0);
    now=h.next_check_ms; beat(&k); kbd_service(&k,now); CHECK(h.fd<0 && h.nparked==1);
    now+=200; kbd_service(&k,now);
    uint8_t r[16][9]; int n=l1_reports(hr2,r,16); CHECK(n==2 && l1_is(r[0],0,0,0,0) && l1_is(r[1],0,0x2A,0,0));
    CHECK(l1_none(hr)); /* nothing more to the stale node */
    key(&k,17,0); CHECK(l1_one(hr2,0,0,0,0));
    /* Released while the node is gone: the new node only gets all-up. */
    key(&k,18,1); CHECK(l1_one(hr2,0,0x28,0,0));
    CHECK(!rename(path,old2)); CHECK(!mkfifo(path,0600)); int hr3=open(path,O_RDONLY|O_NONBLOCK); CHECK(hr3>=0);
    now=h.next_check_ms; beat(&k); kbd_service(&k,now); CHECK(h.fd<0 && h.nparked==2);
    key(&k,18,0); CHECK(l1_none(hr2));
    now+=200; kbd_service(&k,now); CHECK(h.fd>=0 && l1_one(hr3,0,0,0,0) && l1_none(hr2));
    /* Gadget teardown (stop file) with a key held: all-up, closed; reopened all-up; the late up writes nothing. */
    key(&k,19,1); CHECK(l1_one(hr3,0,0x29,0,0));
    int sf=open(stop_path,O_CREAT|O_WRONLY,0600); CHECK(sf>=0); if (sf>=0) close(sf);
    now+=100; beat(&k); kbd_service(&k,now); CHECK(h.fd<0 && l1_one(hr3,0,0,0,0));
    CHECK(!unlink(stop_path)); now+=250; kbd_service(&k,now); CHECK(h.fd>=0 && l1_one(hr3,0,0,0,0));
    key(&k,19,0); CHECK(l1_none(hr3));
    /* Exit (SIGTERM/EOF) path of mk20_kbd.c with a key and the PTT held. */
    key(&k,16,1); key(&k,18,1); CHECK(l1_reports(hr3,r,16)==2);
    CHECK(!hid_release_all(&h,1500)); hid_close(&h,0); CHECK(l1_one(hr3,0,0,0,0));
    hid_close_parked(&h); k.hid=c.hid=NULL; key(&k,18,0); key(&k,16,0);
    close(hr); close(hr2); close(hr3); unlink(path); unlink(old); unlink(old2); CHECK(!rmdir(root)); kbd_free(&k);
}
static void l1_ptt_combo(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0));
    key(&k,17,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0x2A,0,0));
    key(&k,16,0); CHECK(l1_one(hp[0],0,0x2A,0,0)); /* PTT up keeps Backspace */
    key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0));
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0x2A,0,0));
    key(&k,18,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0x2A,0x28,0));
    key(&k,19,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0x2A,0x28,0x29));
    key(&k,18,0); CHECK(l1_one(hp[0],HID_MOD_RALT,0x2A,0,0x29));
    key(&k,17,0); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0x29)); /* Backspace up keeps RAlt + Escape */
    key(&k,19,0); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0));
    key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0));
    CHECK(!l1_host_keys(p[0]));
    /* --ptt elsewhere (here fb17 = col 2,row 4): the PTT position wins; fb16 becomes an ordinary host key. */
    k.ptt_col=1; k.ptt_row=3;
    key(&k,17,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    key(&k,16,1); key(&k,16,0); CHECK(l1_none(hp[0]) && l1_host_keys(p[0])==2);
    key(&k,18,1); CHECK(l1_one(hp[0],0,0x28,0,0)); key(&k,18,0); CHECK(l1_one(hp[0],0,0,0,0));
    l1_teardown(&k,&c,hp,p);
}
static void l1_agent_preempt(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    ctl_set_allowed(&c,1); live(&k,0);
    char r[512], q[256]; const char *acq="{\"op\":\"lease.acquire\",\"ttl_ms\":5000}";
    ctl_handle_line(&c,0,acq,strlen(acq),now,r,sizeof r); const char *t=strstr(r,"\"token\":"); CHECK(t!=NULL);
    long tok=t ? strtol(t+8,NULL,10) : 0;
    snprintf(q,sizeof q,"{\"op\":\"act\",\"token\":%ld,\"seq\":1,\"kind\":\"key\",\"usage\":4,\"down\":1}",tok);
    ctl_handle_line(&c,0,q,strlen(q),now,r,sizeof r); CHECK(strstr(r,"\"ok\":true")!=NULL);
    CHECK(l1_one(hp[0],0,4,0,0));
    /* Physical DELETE: the lease yields to the human (revoke + all-up) before Backspace goes down. */
    key(&k,17,1);
    uint8_t rep[16][9]; int n=l1_reports(hp[0],rep,16);
    CHECK(n==2 && l1_is(rep[0],0,0,0,0) && l1_is(rep[1],0,0x2A,0,0));
    CHECK(!c.token && c.st.preempts==1 && !c.estopped && onboard_current(&k)==ONBOARD_L1);
    snprintf(q,sizeof q,"{\"op\":\"act\",\"token\":%ld,\"seq\":2,\"kind\":\"key\",\"usage\":4,\"down\":1}",tok);
    ctl_handle_line(&c,0,q,strlen(q),now,r,sizeof r); CHECK(strstr(r,"no_lease")!=NULL && l1_none(hp[0]));
    key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    CHECK(!l1_host_keys(p[0]));
    l1_teardown(&k,&c,hp,p);
}
static void round5_regressions(void)
{
    l1_keys_reports(); l1_hold(); l1_inert_pages(); l1_inert_states(); l1_release_paths(); l1_hid_lifetime();
    l1_ptt_combo(); l1_agent_preempt();
}
