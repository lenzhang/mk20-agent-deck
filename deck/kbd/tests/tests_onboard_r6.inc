/* 2026-10-22 L1 option mode (user decision): host ui.hidKeys maps up to 9 of fb1..fb15 to the Mac's
 * digits 1..9 on the live L1 page only. Same gate/ownership/release paths as fb17-19 (round 5), but a
 * digit is momentary (down + up at the press: no auto-repeat), ignored while the Mac PTT is held, and the
 * press (also of fb17-19 while a map is active) is forwarded marked ("hid":usage / "ignored":"ptt").
 * Fake clock, nonblocking pipes; no device paths. */
#define HK_MAP_MS 30000 /* onboard.c HID_MAP_MS */
static long hk_gen = 100, hk_last; /* hk_last: the last generation the device accepted via hk_map */
/* Fresh full frame (ends a grace window) while keeping the host's liveness (seen) current. */
static void hk_fresh(kbd_t *k) { beat(k); l1_fresh(k); beat(k); }
static void hk_drain(int fd) { char f[1][192]; uint32_t id[1]; ux_frames(fd,f,id,0); }
/* Send one ui.hidKeys request; 1 accepted, 0 rejected, -1 no reply. Reads (and drops) every pending host frame. */
static int hk_raw(kbd_t *k, int fd, const char *s, char *reply, size_t cap)
{
    char f[64][192]; uint32_t ids[64]; int ok=-1;
    json(k,s);
    int n=ux_frames(fd,f,ids,64);
    for (int i=0;i<n;i++) if (strstr(f[i],"\"ack_method\":\"ui.hidKeys\"")) {
        ok=strstr(f[i],"\"success\":true")!=NULL;
        if (reply && cap) { size_t l=strlen(f[i]); if (l>=cap) l=cap-1; memcpy(reply,f[i],l); reply[l]=0; }
    }
    return ok;
}
static int hk_map(kbd_t *k, int fd, const char *keys)
{
    char s[512], want[64], reply[192]=""; long g=++hk_gen;
    snprintf(s,sizeof s,"{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":{%s}}",g,keys);
    int ok=hk_raw(k,fd,s,reply,sizeof reply);
    if (ok==1) { snprintf(want,sizeof want,"\"result\":{\"generation\":%ld,\"mapped\":",g); CHECK(strstr(reply,want)!=NULL); hk_last=g; }
    return ok;
}
static const char HK3[]="\"1\":\"1\",\"2\":\"2\",\"3\":\"3\"";
/* keyStateChanged frames since the last read; *hid = those carrying "hid":usage, *down = pressed ones. */
static int hk_keys(int fd, int usage, int *hid, int *down)
{
    char f[64][192]; uint32_t ids[64]; int n=ux_frames(fd,f,ids,64), c=0; char want[24];
    snprintf(want,sizeof want,"\"hid\":%d,",usage); *hid=0; if (down) *down=0;
    for (int i=0;i<n;i++) if (strstr(f[i],"keyStateChanged")) {
        c++; if (strstr(f[i],want)) (*hid)++;
        if (down && strstr(f[i],"\"pressed\":true")) (*down)++;
        if (strstr(f[i],"\"hid\":") || strstr(f[i],"\"ignored\":"))
            CHECK(strstr(f[i],"\"pressed\":true")!=NULL); /* only presses carry a mark */
    }
    return c;
}
/* Exactly two new reports: a = (mod, a0..a2) then b = (mod, b0..b2) -- a momentary digit. */
static int hk_two(int fd, int mod, int a0, int a1, int a2, int b0, int b1, int b2)
{ uint8_t r[16][9]; int n=l1_reports(fd,r,16); return n==2 && l1_is(r[0],mod,a0,a1,a2) && l1_is(r[1],mod,b0,b1,b2); }
/* One mapped press + release on a live L1: the press types the digit (down + up), the release sends no
 * HID; the host gets one marked press + one plain up. */
static int hk_digit_ok(kbd_t *k, int hp, int p, int fb, int usage)
{
    int hid=0, down=0, ok=1;
    key(k,fb,1); ok&=hk_two(hp,0,usage,0,0,0,0,0);
    key(k,fb,0); ok&=l1_none(hp);
    ok&=hk_keys(p,usage,&hid,&down)==2 && hid==1 && down==1;
    return ok;
}
/* A key that behaves as today: a plain forwarded host key, no HID. */
static int hk_plain(kbd_t *k, int hp, int p, int fb)
{
    key(k,fb,1); key(k,fb,0);
    char f[64][192]; uint32_t ids[64]; int n=ux_frames(p,f,ids,64), c=0, marked=0;
    for (int i=0;i<n;i++) if (strstr(f[i],"keyStateChanged")) { c++; marked+=strstr(f[i],"\"hid\":")!=NULL; }
    return l1_none(hp) && c==2 && !marked;
}

static void hk_validate(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    char reply[192];
    /* hello advertises the capability and the last accepted option-key generation */
    hk_drain(p[0]);
    json(&k,"{\"method\":\"ui.hello\",\"version\":1}");
    { char f[64][192]; uint32_t ids[64]; int n=ux_frames(p[0],f,ids,64), seen=0;
      for (int i=0;i<n;i++) if (strstr(f[i],"\"ack_method\":\"ui.hello\"")) seen += strstr(f[i],"\"ui.hidKeys\"]")!=NULL && strstr(f[i],"\"hid_generation\":0}")!=NULL;
      CHECK(seen==1); }
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); jpeg(&k); finish_decode(&k); CHECK(onboard_tile_allowed(&k,1,0,1));
    hk_drain(p[0]);
    CHECK(hk_map(&k,p[0],HK3)==1);
    CHECK(hk_digit_ok(&k,hp[0],p[0],1,0x1E) && hk_digit_ok(&k,hp[0],p[0],2,0x1F) && hk_digit_ok(&k,hp[0],p[0],3,0x20));
    CHECK(hk_plain(&k,hp[0],p[0],4) && hk_plain(&k,hp[0],p[0],15)); /* unmapped slots: as today */
    /* Every malformed / out-of-range request is rejected and leaves the accepted map untouched. */
    const char *bad[] = {
        "\"0\":\"1\"", "\"16\":\"1\"", "\"20\":\"1\"", "\"01\":\"1\"", "\"fb1\":\"1\"", "\"1\":\"0\"", "\"1\":\"10\"", "\"1\":\"a\"",
        "\"1\":1", "\"1\":true", "\"1\":null", "\"1\":\"\"", "\"1\":\"1\",\"2\":\"1\"", "\"1\":\"1\",\"1\":\"2\"", "\"1\":{\"a\":\"1\"}",
        "\"1\":[\"1\"]", "\"1\":\"1\",", "\"1\":\"\\u0000\"", "\"-1\":\"1\"", "\"1 \":\"1\"",
        "\"1\":\"1\",\"2\":\"2\",\"3\":\"3\",\"4\":\"4\",\"5\":\"5\",\"6\":\"6\",\"7\":\"7\",\"8\":\"8\",\"9\":\"9\",\"10\":\"1\"",
    };
    for (size_t i=0;i<sizeof bad/sizeof *bad;i++) CHECK(hk_map(&k,p[0],bad[i])==0);
    char s[512]; long g=++hk_gen;
    const char *bad_whole[] = {
        "{\"method\":\"ui.hidKeys\",\"generation\":%ld}", "{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":\"1\"}",
        "{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":[]}", "{\"method\":\"ui.hidKeys\",\"generation\":\"%ld\",\"keys\":{}}",
        "{\"method\":\"ui.hidKeys\",\"keys\":{},\"x\":%ld}", "{\"method\":\"ui.hidKeys\",\"generation\":-%ld,\"keys\":{}}",
        "{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":{}}x", "{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":{},\"keys\":{}}",
        "{\"method\":\"ui.hidKeys\",\"generation\":%ld.5,\"keys\":{}}", "{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":{\"1\":{\"2\":{}}}}",
        "{\"method\":\"ui.hidKeys\",\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":{}}",
        "{\"method\":\"ui.hidKeys\",\"generation\":%ld000000000,\"keys\":{}}",
    };
    for (size_t i=0;i<sizeof bad_whole/sizeof *bad_whole;i++) { snprintf(s,sizeof s,bad_whole[i],g); CHECK(hk_raw(&k,p[0],s,NULL,0)==0); }
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":0,\"keys\":{}}",NULL,0)==0);
    /* Stale / repeated generation: rejected (strictly increasing). */
    for (int d=0;d<2;d++) {
        snprintf(s,sizeof s,"{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":{\"5\":\"5\"}}",hk_last-d);
        CHECK(hk_raw(&k,p[0],s,NULL,0)==0);
    }
    CHECK(hk_digit_ok(&k,hp[0],p[0],1,0x1E) && hk_plain(&k,hp[0],p[0],5)); /* still the HK3 map */
    snprintf(s,sizeof s,"{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":{\"5\":\"5\"}}",++hk_gen);
    CHECK(hk_raw(&k,p[0],s,NULL,0)==1);
    CHECK(hk_raw(&k,p[0],s,NULL,0)==0); /* same generation again */
    CHECK(hk_digit_ok(&k,hp[0],p[0],5,0x22) && hk_plain(&k,hp[0],p[0],1)); /* a new map replaces the old one */
    /* All nine digits, any of fb1..fb15; rows 2-3 too. Whitespace and escapes are ordinary JSON. */
    CHECK(hk_map(&k,p[0],"\"1\":\"9\",\"2\":\"8\",\"3\":\"7\",\"4\":\"6\",\"5\":\"5\",\"11\":\"4\",\"12\":\"3\",\"14\":\"2\",\"15\":\"\\u0031\"")==1);
    static const int fbs[9]={1,2,3,4,5,11,12,14,15}, digit[9]={9,8,7,6,5,4,3,2,1};
    for (int i=0;i<9;i++) CHECK(hk_digit_ok(&k,hp[0],p[0],fbs[i],0x1E + digit[i] - 1));
    CHECK(hk_plain(&k,hp[0],p[0],6) && hk_plain(&k,hp[0],p[0],13));
    snprintf(s,sizeof s," { \"keys\" : { \"7\" : \"3\" } , \"generation\" : %ld , \"method\" : \"ui.hidKeys\" } ",++hk_gen);
    CHECK(hk_raw(&k,p[0],s,reply,sizeof reply)==1 && strstr(reply,"\"mapped\":1}")!=NULL);
    CHECK(hk_digit_ok(&k,hp[0],p[0],7,0x20));
    /* {} clears: every slot is a plain host key again; fb17 is Backspace again and not forwarded. */
    CHECK(hk_map(&k,p[0],"")==1);
    for (int fb=1;fb<=15;fb++) CHECK(hk_plain(&k,hp[0],p[0],fb));
    key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0)); key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0)); CHECK(!l1_host_keys(p[0]));
    /* fb16 PTT, fb18-19 and fb20 never take a digit. */
    CHECK(hk_map(&k,p[0],"\"16\":\"1\"")==0 && hk_map(&k,p[0],"\"17\":\"1\"")==0 && hk_map(&k,p[0],"\"20\":\"1\"")==0);
    /* Without a negotiated host (legacy getInfo liveness) the method is refused. */
    onboard_loss(&k); json(&k,"{\"method\":\"getInfo\"}"); CHECK(onboard_health(&k)==HOST_PROBING);
    hk_drain(p[0]);
    CHECK(hk_map(&k,p[0],HK3)==0 && hk_map(&k,p[0],"")==0);
    l1_teardown(&k,&c,hp,p);
}
/* Forwarding and the bottom row while a map is active; PTT combinations (one boot report). */
static void hk_reports(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); int hid, down;
    CHECK(hk_map(&k,p[0],HK3)==1);
    /* fb17-19 keep their HID and are now forwarded too (marked), so the Mac sees Escape/Return in option mode. */
    for (int i=0;i<3;i++) {
        key(&k,l1_fb[i],1); CHECK(l1_one(hp[0],0,l1_usage[i],0,0));
        key(&k,l1_fb[i],0); CHECK(l1_one(hp[0],0,0,0,0));
        CHECK(hk_keys(p[0],l1_usage[i],&hid,&down)==2 && hid==1 && down==1);
    }
    /* A digit is momentary around a held key (Escape stays down); fb17-19 remain held keys. */
    key(&k,19,1); CHECK(l1_one(hp[0],0,0x29,0,0));
    key(&k,2,1); CHECK(hk_two(hp[0],0,0x29,0x1F,0,0x29,0,0));
    key(&k,19,0); CHECK(l1_one(hp[0],0,0,0,0));
    key(&k,2,0); CHECK(l1_none(hp[0]));
    /* Mac PTT held: digits are ignored (Right Option + digit types a symbol) and forwarded as "ignored":"ptt";
     * fb17-19 keep combining with RAlt. A digit pressed under the PTT types nothing later either. */
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0));
    key(&k,1,1); key(&k,1,0); CHECK(l1_none(hp[0]));
    key(&k,18,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0x28,0,0)); key(&k,18,0); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0));
    key(&k,3,1); CHECK(l1_none(hp[0]));
    key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0));
    key(&k,3,0); CHECK(l1_none(hp[0]));
    key(&k,3,1); CHECK(hk_two(hp[0],0,0x20,0,0,0,0,0)); key(&k,3,0); CHECK(l1_none(hp[0])); /* PTT up: typed again */
    CHECK(!h.mods && !h.keys[0] && !h.keys[1] && !h.keys[2]);
    { char f[64][192]; uint32_t ids[64]; int n=ux_frames(p[0],f,ids,64);
      CHECK(ux_count(f,n,"keyStateChanged")==12 && ux_count(f,n,"\"ignored\":\"ptt\"")==2 &&
            ux_count(f,n,"\"hid\":41,")==1 && ux_count(f,n,"\"hid\":31,")==1 && ux_count(f,n,"\"hid\":40,")==1 &&
            ux_count(f,n,"\"hid\":32,")==1 && ux_count(f,n,"\"pressed\":false")==6); } /* the PTT itself never */
    /* --no-hid: a mapped press is still consumed and forwarded marked (the host sees what was asked). */
    k.hid=c.hid=NULL; key(&k,3,1); key(&k,3,0); k.hid=c.hid=&h;
    CHECK(l1_none(hp[0]) && hk_keys(p[0],0x20,&hid,&down)==2 && hid==1);
    /* --ptt on a mapped slot: the PTT position wins over the map. */
    k.ptt_col=0; k.ptt_row=0;
    key(&k,1,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); key(&k,1,0); CHECK(l1_one(hp[0],0,0,0,0));
    CHECK(!l1_host_keys(p[0]));
    k.ptt_col=0; k.ptt_row=3;
    /* Agent lease: a physical digit takes input back first (revoke + all-up), then the digit goes down. */
    ctl_set_allowed(&c,1); live(&k,0); hk_drain(p[0]);
    CHECK(hk_map(&k,p[0],HK3)==1);
    char r[512]; const char *acq="{\"op\":\"lease.acquire\",\"ttl_ms\":5000}";
    ctl_handle_line(&c,0,acq,strlen(acq),now,r,sizeof r); const char *t=strstr(r,"\"token\":"); CHECK(t!=NULL);
    char q[256]; snprintf(q,sizeof q,"{\"op\":\"act\",\"token\":%ld,\"seq\":1,\"kind\":\"key\",\"usage\":4,\"down\":1}",t ? strtol(t+8,NULL,10) : 0L);
    ctl_handle_line(&c,0,q,strlen(q),now,r,sizeof r); CHECK(l1_one(hp[0],0,4,0,0));
    key(&k,1,1);
    { uint8_t rep[16][9]; int n=l1_reports(hp[0],rep,16);
      CHECK(n==3 && l1_is(rep[0],0,0,0,0) && l1_is(rep[1],0,0x1E,0,0) && l1_is(rep[2],0,0,0,0)); }
    CHECK(!c.token && !c.estopped); key(&k,1,0); CHECK(l1_none(hp[0]));
    l1_teardown(&k,&c,hp,p);
}
/* Gate: only the live L1 page (ONLINE + fresh frame); never L4/HOME/LS/L5/agent, offline, PROBING,
 * SUSPECT, grace or the post-e-stop pause. */
static void hk_gate(void)
{
    char f[64][192]; uint32_t ids[64];
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    /* L4 (after K2): rejected; slots keep their meaning (plain host keys, row 1 = mode keys). */
    knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); hk_fresh(&k); ux_frames(p[0],f,ids,64);
    CHECK(hk_map(&k,p[0],HK3)==0 && hk_map(&k,p[0],"")==1);
    CHECK(hk_plain(&k,hp[0],p[0],1) && hk_plain(&k,hp[0],p[0],7));
    /* Back on L1, during the page-switch grace window: accepted, but presses are swallowed until a fresh frame. */
    knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L1); ux_frames(p[0],f,ids,64);
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,1,1); key(&k,1,0); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    hk_fresh(&k); CHECK(hk_digit_ok(&k,hp[0],p[0],1,0x1E));
    /* Device-rendered pages: refused; mapped slots there keep their on-board meaning. */
    static const enum onboard_route pages[] = {ONBOARD_HOME, ONBOARD_LS, ONBOARD_L5};
    for (int i=0;i<3;i++) {
        onboard_route(&k,ONBOARD_L1); hk_fresh(&k); CHECK(hk_map(&k,p[0],HK3)==1);
        onboard_route(&k,pages[i]); pump(&k); beat(&k); ux_frames(p[0],f,ids,64);
        CHECK(hk_map(&k,p[0],HK3)==0);
        key(&k,3,1); key(&k,3,0); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    }
    /* SUSPECT (3 s silence): the map is gone and cannot be set; nothing reaches the Mac. */
    onboard_route(&k,ONBOARD_L1); hk_fresh(&k); CHECK(hk_map(&k,p[0],HK3)==1);
    CHECK(onboard_hid_keys(&k)==3);
    now+=3000; kbd_safety_tick(&k,now); CHECK(onboard_health(&k)==HOST_SUSPECT && !onboard_hid_keys(&k)); ux_frames(p[0],f,ids,64);
    CHECK(hk_map(&k,p[0],HK3)==0);
    key(&k,1,1); key(&k,1,0); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    /* Offline: refused (handshake required); PROBING after a new hello: refused until ONLINE. */
    onboard_loss(&k); CHECK(hk_map(&k,p[0],HK3)==0 && hk_map(&k,p[0],"")==0);
    key(&k,1,1); key(&k,1,0); CHECK(l1_none(hp[0]));
    hello(&k); beat(&k); CHECK(onboard_health(&k)==HOST_PROBING); ux_frames(p[0],f,ids,64);
    CHECK(hk_map(&k,p[0],HK3)==0 && hk_map(&k,p[0],"")==1); /* clearing is always fine once negotiated */
    now+=1000; beat(&k); now+=1000; beat(&k); CHECK(onboard_health(&k)==HOST_ONLINE); ux_frames(p[0],f,ids,64);
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,1,1); key(&k,1,0); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0])); /* placeholder: no fresh frame yet */
    hk_fresh(&k); CHECK(hk_digit_ok(&k,hp[0],p[0],1,0x1E));
    /* A stale request queued in the tty before a route boundary is refused like a stale ui.page. */
    const char *stale="{\"method\":\"ui.hidKeys\",\"generation\":999999990,\"keys\":{\"1\":\"1\"}}";
    uint8_t wire[256]; int len=host_encode(5,101,(const uint8_t *)stale,strlen(stale),wire,sizeof wire); CHECK(len>0);
    hk_drain(p[0]); /* boundary() measures unread host bytes with FIONREAD (here: our own pipe) */
    kbd_host_feed(&k,wire,10); onboard_route(&k,ONBOARD_HOME); onboard_route(&k,ONBOARD_L1); kbd_host_feed(&k,wire+10,(size_t)len-10);
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"ack_method\":\"ui.hidKeys\",\"success\":false")==1); }
    hk_fresh(&k); CHECK(hk_plain(&k,hp[0],p[0],1));
    CHECK(hk_map(&k,p[0],HK3)==1); /* the stale generation was not consumed */
    l1_teardown(&k,&c,hp,p);
    /* Post-e-stop pause: map cleared by the stop, refused during the pause, fine afterwards. */
    l1_setup(&k,&c,&h,hp,p); CHECK(hk_map(&k,p[0],HK3)==1);
    req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped && onboard_current(&k)==ONBOARD_L1);
    ux_frames(p[0],f,ids,64); { uint8_t r[16][9]; l1_reports(hp[0],r,16); }
    CHECK(hk_map(&k,p[0],HK3)==0);
    key(&k,1,1); key(&k,1,0); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    live(&k,1000); ux_frames(p[0],f,ids,64);
    CHECK(hk_map(&k,p[0],HK3)==1 && hk_digit_ok(&k,hp[0],p[0],2,0x1F)); /* latched stop, pause over: like fb17-19 */
    l1_teardown(&k,&c,hp,p);
}
/* Every clear path ends option mode. Held at that moment: fb17 (Backspace, a forwarded held key: all-up
 * where a page boundary/SUSPECT/e-stop releases fb17-19, else its own up) and a digit (momentary: already
 * up on the Mac, only its ownership remains; its late up never types anything). */
static void hk_clear_paths(void)
{
    char f[64][192]; uint32_t ids[64];
    for (int mode=0;mode<14;mode++) {
        int fails0=fails;
        kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
        if (mode==0) { ctl_set_allowed(&c,1); live(&k,0); ux_frames(p[0],f,ids,64); }
        CHECK(hk_map(&k,p[0],HK3)==1 && onboard_hid_keys(&k)==3);
        if (mode==13) { CHECK(hk_digit_ok(&k,hp[0],p[0],2,0x1F)); } /* expiry: nothing held (the 10 s cut is separate) */
        else { key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0)); key(&k,1,1); CHECK(hk_two(hp[0],0,0x2A,0x1E,0,0x2A,0,0)); }
        ux_frames(p[0],f,ids,64);
        int boundary_up=1;
        switch (mode) {
        case 0: key(&k,20,1); CHECK(c.estopped); key(&k,20,0); break;                                   /* fb20 e-stop */
        case 1: knob(&k,103,1); now+=1000; kbd_service(&k,now); CHECK(c.estopped); knob(&k,103,0); break; /* K2 hold */
        case 2: req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped); break;   /* socket */
        case 3: knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); break;   /* K2 short */
        case 4: json(&k,"{\"method\":\"ui.page\",\"page\":\"L1\",\"generation\":9}"); CHECK(onboard_current(&k)==ONBOARD_L1); break;
        case 5: knob(&k,100,1); now+=1000; kbd_service(&k,now); knob(&k,100,0); CHECK(onboard_current(&k)==ONBOARD_HOME); break;
        case 6: onboard_loss(&k); CHECK(onboard_health(&k)==HOST_OFFLINE); break;
        case 7: now+=3000; kbd_safety_tick(&k,now); CHECK(onboard_health(&k)==HOST_SUSPECT); break;
        case 8: json(&k,"{\"method\":\"ui.hello\",\"version\":1}"); break;
        case 9: c.screen.current=1; kbd_safety_tick(&k,now); CHECK(onboard_current(&k)==ONBOARD_L5); break; /* ctl page */
        case 10: onboard_route(&k,ONBOARD_HOME); break;
        case 11: json(&k,"{\"method\":\"ui.page\",\"page\":\"L4\",\"generation\":9}"); CHECK(onboard_current(&k)==ONBOARD_L4); break;
        case 12: CHECK(hk_map(&k,p[0],"")==1); boundary_up=0; break;                                      /* host clear */
        case 13: live(&k,HK_MAP_MS-2000); ux_frames(p[0],f,ids,64); CHECK(hk_digit_ok(&k,hp[0],p[0],3,0x20)); /* 29 s */
                 live(&k,0); boundary_up=-1; break;                                                        /* 30 s expiry */
        }
        CHECK(!onboard_hid_keys(&k));
        if (boundary_up == 1) {
            uint8_t r[16][9]; int n=l1_reports(hp[0],r,16);
            CHECK(n>=1 && l1_is(r[n-1],0,0,0,0) && !h.keys[0] && !h.keys[1]);
            key(&k,17,0); key(&k,1,0); CHECK(l1_none(hp[0])); /* late physical ups: nothing */
        } else if (!boundary_up) {
            CHECK(l1_none(hp[0]) && h.keys[0]==0x2A && !h.keys[1]); /* Backspace keeps its press-time owner */
            key(&k,1,0); CHECK(l1_none(hp[0]));
            key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
        }
        /* Map gone: once the page is live L1 again, fb1-fb3 are plain host keys (or nothing elsewhere). */
        if (mode==3 || mode==11) { hk_fresh(&k); knob(&k,103,1); now+=20; knob(&k,103,0); } /* no K2 during grace */
        if (mode==5 || mode==9 || mode==10) onboard_route(&k,ONBOARD_L1);
        if (mode==6 || mode==7 || mode==8) { onboard_loss(&k); hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); }
        if (mode<=2) live(&k,1000);
        if (mode!=12 && mode!=13) hk_fresh(&k);
        ux_frames(p[0],f,ids,64);
        CHECK(onboard_current(&k)==ONBOARD_L1 && onboard_health(&k)==HOST_ONLINE);
        for (int fb=1;fb<=3;fb++) CHECK(hk_plain(&k,hp[0],p[0],fb));
        if (fails!=fails0) fprintf(stderr,"hk_clear_paths: mode %d failed\n",mode);
        l1_teardown(&k,&c,hp,p);
    }
}
/* 30 s lifetime after the last accepted request; refreshes extend it; the loop wakes for it. */
static void hk_expiry(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); char f[64][192]; uint32_t ids[64];
    CHECK(hk_map(&k,p[0],HK3)==1);
    pump(&k); int t=onboard_timeout(&k,now,60000); CHECK(t>0 && t<=HK_MAP_MS);
    live(&k,8000); ux_frames(p[0],f,ids,64); CHECK(hk_map(&k,p[0],HK3)==1); /* refresh R at ~9 s */
    live(&k,20000); ux_frames(p[0],f,ids,64); CHECK(hk_digit_ok(&k,hp[0],p[0],2,0x1F)); /* 30 s after the first, R+21 s */
    live(&k,7000); ux_frames(p[0],f,ids,64); CHECK(hk_digit_ok(&k,hp[0],p[0],3,0x20)); /* R+29 s */
    live(&k,0); ux_frames(p[0],f,ids,64); CHECK(hk_plain(&k,hp[0],p[0],3) && hk_plain(&k,hp[0],p[0],1)); /* R+30 s */
    /* Expired: a fresh request works again (generation keeps increasing). */
    CHECK(hk_map(&k,p[0],"\"9\":\"4\"")==1 && hk_digit_ok(&k,hp[0],p[0],9,0x21));
    l1_teardown(&k,&c,hp,p);
}
/* Generations: strictly increasing across hello/loss until process restart, independent of ui.page. */
static void hk_generation(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); char f[64][192]; uint32_t ids[64];
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":50,\"keys\":{\"1\":\"1\"}}",NULL,0)==1);
    json(&k,"{\"method\":\"ui.page\",\"page\":\"L1\",\"generation\":3}"); hk_fresh(&k); /* page counter is separate */
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"result\":{\"page\":\"L1\",\"generation\":3}")==1); }
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":51,\"keys\":{\"1\":\"1\"}}",NULL,0)==1);
    json(&k,"{\"method\":\"ui.hello\",\"version\":1}");
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"generation\":3,\"hid_generation\":51}")==1); }
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); hk_fresh(&k); ux_frames(p[0],f,ids,64);
    CHECK(hk_plain(&k,hp[0],p[0],1)); /* hello cleared the map */
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":51,\"keys\":{\"1\":\"1\"}}",NULL,0)==0);
    onboard_loss(&k); hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); hk_fresh(&k); ux_frames(p[0],f,ids,64);
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":40,\"keys\":{\"1\":\"1\"}}",NULL,0)==0);
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":999999999,\"keys\":{\"1\":\"1\"}}",NULL,0)==1);
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":999999999,\"keys\":{}}",NULL,0)==0); /* exhausted */
    CHECK(hk_digit_ok(&k,hp[0],p[0],1,0x1E));
    l1_teardown(&k,&c,hp,p);
}
/* Hold: one down while held, the 10 s lost-key-up cut also sends the host its up; host loss mid-press. */
static void hk_hold(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); int hid, down;
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,2,1); CHECK(hk_two(hp[0],0,0x1F,0,0,0,0,0)); /* typed once, at the press: no auto-repeat */
    key(&k,2,1); CHECK(l1_none(hp[0])); /* repeated down (lost GD32 up): the same press, not typed/forwarded again */
    CHECK(hk_keys(p[0],0x1F,&hid,&down)==1 && hid==1);
    pump(&k); int t=onboard_timeout(&k,now,60000); CHECK(t>0 && t<=L1_HOLD_MS-250);
    live(&k,8000); CHECK(l1_none(hp[0])); /* held 9 s: exactly one digit */
    live(&k,0); CHECK(l1_none(hp[0])); /* 10 s: the ownership guard ends the press (nothing left to release) ... */
    CHECK(hk_keys(p[0],0x1F,&hid,&down)==1 && !hid && !down); /* ... and the host got the up */
    key(&k,2,0); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
    CHECK(hk_digit_ok(&k,hp[0],p[0],2,0x1F)); /* press again: typed again */
    /* No host link at all (EOF): the digit still goes up on release, nothing is queued. */
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,3,1); CHECK(hk_two(hp[0],0,0x20,0,0,0,0,0)); l1_host_keys(p[0]);
    int saved=k.host_fd; k.host_fd=-1; key(&k,3,0); CHECK(l1_none(hp[0])); k.host_fd=saved;
    CHECK(!l1_host_keys(p[0]) && hk_digit_ok(&k,hp[0],p[0],3,0x20)); /* ownership cleared without a host */
    l1_teardown(&k,&c,hp,p);
}
static void round6_regressions(void)
{
    hk_validate(); hk_reports(); hk_gate(); hk_clear_paths(); hk_expiry(); hk_generation(); hk_hold();
}
