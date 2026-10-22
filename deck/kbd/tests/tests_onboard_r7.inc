/* 1.6.0 host protocol additions (integrator brief 2026-10-22):
 *  A. a same-page ui.page only redraws: option map cleared, content invalidated (fresh frame, grace,
 *     stale receive barrier), but held keys keep their press-time owners and HID state;
 *  B. the ui.hello reply names the route ("page": L1/L4/none); a host page changed while no host was
 *     ONLINE is announced by the K2 tap before that reply (no longer at ONLINE);
 *  C. a fresh ui.hello resets generation and hid_generation to 0 (both reported as before);
 *  D. LS fb17 会话安排 sends exactly {"method":"ui.openArrange"} (ONLINE only, own 3 s limiter).
 * Fake clock, nonblocking pipes; no device paths. */
static long v7_gen = 5000;
/* One cmd101 request through the normal host frame path; every queued host frame is returned. */
static int v7_req(kbd_t *k, int fd, const char *s, char f[][192], uint32_t ids[])
{ json(k,s); return ux_frames(fd,f,ids,64); }
/* ui.page with a fresh generation: 1 accepted (reply echoes page + generation), 0 refused, -1 no reply.
 * *ups = keyStateChanged releases among the frames it produced (a boundary would send them). */
static int v7_page(kbd_t *k, int fd, const char *page, int *ups)
{
    char s[160], f[64][192], want[96]; uint32_t ids[64]; long g=++v7_gen; int ok=-1;
    snprintf(s,sizeof s,"{\"method\":\"ui.page\",\"page\":\"%s\",\"generation\":%ld}",page,g);
    int n=v7_req(k,fd,s,f,ids);
    snprintf(want,sizeof want,"\"result\":{\"page\":\"%s\",\"generation\":%ld}",page,g);
    for (int i=0;i<n;i++) if (strstr(f[i],"\"ack_method\":\"ui.page\"")) ok=strstr(f[i],want)!=NULL;
    if (ups) *ups=ux_count(f,n,"\"pressed\":false");
    return ok;
}
static int v7_ups(int fd) { char f[64][192]; uint32_t ids[64]; int n=ux_frames(fd,f,ids,64); return ux_count(f,n,"\"pressed\":false"); }

/* A: PTT + Backspace + Escape (Mac HID), a host key and the Mac mode key held across a same-page ui.page. */
static void v7_same_page_keeps_held(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); int ups; char f[64][192]; uint32_t ids[64];
    for (int page=0;page<2;page++) {
        const char *name = page ? "L4" : "L1";
        key(&k,16,1); key(&k,17,1); key(&k,19,1);
        { uint8_t r[16][9]; CHECK(l1_reports(hp[0],r,16)==3 && l1_is(r[2],HID_MOD_RALT,0x2A,0x29,0)); }
        key(&k,12,1); key(&k,20,1); /* forwarded host key + Mac mode key (agent inactive): owner 1 */
        { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"pressed\":true")==2); }
        /* a tile staged for the old content is dropped with it */
        tile(&k,1,0,1);
        CHECK(v7_page(&k,p[0],name,&ups)==1 && !ups);
        CHECK(onboard_current(&k)==(page ? ONBOARD_L4 : ONBOARD_L1) && onboard_health(&k)==HOST_ONLINE);
        CHECK(l1_none(hp[0]) && h.mods==HID_MOD_RALT && h.keys[0]==0x2A && h.keys[1]==0x29); /* nothing released */
        CHECK(!onboard_tile_allowed(&k,1,0,1)); /* invalidated: a fresh full frame is required */
        for (int i=0;i<64;i++) kbd_service(&k,now);
        CHECK(k.scr.pending[0][0]!=0xffff);
        /* grace window: a new press is swallowed (no HID, no host event, no navigation) */
        key(&k,18,1); key(&k,18,0); key(&k,6,1); key(&k,6,0);
        CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]) && onboard_current(&k)==(page ? ONBOARD_L4 : ONBOARD_L1));
        /* repeated GD32 downs of the held keys while the page waits: the same presses (no RAlt leak, no lost up) */
        key(&k,16,1); key(&k,12,1); key(&k,20,1); key(&k,17,1);
        CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
        hk_fresh(&k); CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
        /* physical releases end each one, with its own report / host up */
        key(&k,17,0); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0x29,0));
        key(&k,19,0); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0));
        key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0));
        key(&k,12,0); key(&k,20,0); CHECK(v7_ups(p[0])==2 && l1_none(hp[0]));
        key(&k,17,0); key(&k,16,0); CHECK(l1_none(hp[0]) && !v7_ups(p[0])); /* duplicate ups: nothing */
        /* live again: the keys work as before */
        key(&k,18,1); CHECK(l1_one(hp[0],0,0x28,0,0)); key(&k,18,0); CHECK(l1_one(hp[0],0,0,0,0));
        if (!page) { knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); hk_fresh(&k); ux_frames(p[0],f,ids,64); }
    }
    /* A different page keeps the full boundary: all-up, host ups at once, late physical ups do nothing. */
    key(&k,16,1); key(&k,17,1); key(&k,12,1); { uint8_t r[16][9]; l1_reports(hp[0],r,16); } l1_host_keys(p[0]);
    CHECK(v7_page(&k,p[0],"L1",&ups)==1 && ups==1 && onboard_current(&k)==ONBOARD_L1);
    { uint8_t r[16][9]; int n=l1_reports(hp[0],r,16); CHECK(n>=1 && l1_is(r[n-1],0,0,0,0) && !h.mods && !h.keys[0]); }
    key(&k,16,0); key(&k,17,0); key(&k,12,0); CHECK(l1_none(hp[0]) && !v7_ups(p[0]));
    l1_teardown(&k,&c,hp,p);
}
/* A with option mode: the map is cleared in both cases; a held digit / forwarded fb17 keep their owners. */
static void v7_same_page_option_mode(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); int ups, hid, down;
    CHECK(hk_map(&k,p[0],HK3)==1 && onboard_hid_keys(&k)==3);
    key(&k,1,1); CHECK(hk_two(hp[0],0,0x1E,0,0,0,0,0)); /* digit typed at the press (owner 7) */
    key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0));      /* Backspace, also forwarded (owner 6) */
    CHECK(hk_keys(p[0],0x2A,&hid,&down)==2 && down==2);
    CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups && !onboard_hid_keys(&k)); /* map cleared, nothing released */
    CHECK(l1_none(hp[0]) && h.keys[0]==0x2A);
    hk_fresh(&k);
    key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0)); /* HID up, then the host up */
    key(&k,1,0); CHECK(l1_none(hp[0]));         /* the digit went up at its press: only the host up */
    CHECK(v7_ups(p[0])==2);
    for (int fb=1;fb<=3;fb++) CHECK(hk_plain(&k,hp[0],p[0],fb)); /* map gone until the Mac sends it again */
    /* different page: map cleared too, held keys released at once */
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,2,1); key(&k,17,1); { uint8_t r[16][9]; l1_reports(hp[0],r,16); } l1_host_keys(p[0]);
    CHECK(v7_page(&k,p[0],"L4",&ups)==1 && ups==2 && !onboard_hid_keys(&k) && onboard_current(&k)==ONBOARD_L4);
    { uint8_t r[16][9]; int n=l1_reports(hp[0],r,16); CHECK(n>=1 && l1_is(r[n-1],0,0,0,0)); }
    key(&k,2,0); key(&k,17,0); CHECK(l1_none(hp[0]) && !v7_ups(p[0]));
    l1_teardown(&k,&c,hp,p);
}
/* A: the other release paths still end a key held across a same-page request. */
static void v7_same_page_release_paths(void)
{
    char f[64][192]; uint32_t ids[64];
    for (int mode=0;mode<5;mode++) {
        kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); int ups;
        key(&k,16,1); key(&k,18,1); { uint8_t r[16][9]; CHECK(l1_reports(hp[0],r,16)==2); }
        CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups && l1_none(hp[0]));
        switch (mode) {
        case 0: live(&k,8000); CHECK(l1_none(hp[0])); live(&k,0); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); /* 10 s cut (Return) */
                key(&k,18,0); key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0)); break;                          /* the PTT is not cut */
        case 1: req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped); break; /* socket e-stop */
        case 2: knob(&k,103,1); now+=1000; kbd_service(&k,now); CHECK(c.estopped); knob(&k,103,0); break; /* K2 hold */
        case 3: now+=3000; kbd_safety_tick(&k,now); CHECK(onboard_health(&k)==HOST_SUSPECT); break;        /* SUSPECT */
        case 4: json(&k,"{\"method\":\"ui.hello\",\"version\":1}"); break;                                  /* hello */
        }
        if (mode) {
            uint8_t r[16][9]; int n=l1_reports(hp[0],r,16);
            CHECK(n>=1 && l1_is(r[n-1],0,0,0,0) && !h.mods && !h.keys[0]);
            key(&k,18,0); key(&k,16,0); CHECK(l1_none(hp[0]));
        }
        ux_frames(p[0],f,ids,64);
        l1_teardown(&k,&c,hp,p);
    }
    /* Same-page request during the post-e-stop pause: the pause and the swallowed presses are unchanged. */
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); int ups;
    key(&k,12,1); l1_host_keys(p[0]);
    req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped); { uint8_t r[16][9]; l1_reports(hp[0],r,16); }
    key(&k,17,1); CHECK(l1_none(hp[0]));
    CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups);
    key(&k,17,0); key(&k,12,0); CHECK(l1_none(hp[0]) && v7_ups(p[0])==1); /* pressed before the stop: its up arrives */
    live(&k,1000); hk_fresh(&k); key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0)); key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    l1_teardown(&k,&c,hp,p);
}
/* B: the hello reply names the route; a page changed while no host was ONLINE is tapped before the reply. */
static int v7_hello(kbd_t *k, int fd, const char *page, int *tap_before, int *taps)
{
    char f[64][192], want[48]; uint32_t ids[64]; int n=v7_req(k,fd,"{\"method\":\"ui.hello\",\"version\":1}",f,ids), ack=-1;
    snprintf(want,sizeof want,"\"page\":\"%s\",\"generation\":0,",page);
    *taps=0; *tap_before=0;
    for (int i=0;i<n;i++) {
        if (strstr(f[i],"\"col\":103,")) { (*taps)++; if (ack<0) (*tap_before)++; CHECK(ids[i]==KBD_EVENT_ID); }
        if (strstr(f[i],"\"ack_method\":\"ui.hello\"") && strstr(f[i],want) && strstr(f[i],"\"hid_generation\":0}}")) ack=i;
    }
    return ack>=0;
}
static void v7_hello_page(void)
{
    char f[64][192]; uint32_t ids[64]; int before, taps;
    kbd_t k; ctl_t c; init(&k,&c);
    int p[2]; CHECK(!pipe(p)); CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK)); k.host_fd=p[1];
    CHECK(v7_hello(&k,p[0],"none",&before,&taps) && !taps); /* boot HOME */
    static const enum onboard_route own[] = {ONBOARD_LS, ONBOARD_L5};
    for (int i=0;i<2;i++) { onboard_route(&k,own[i]); CHECK(v7_hello(&k,p[0],"none",&before,&taps) && !taps); }
    onboard_route(&k,ONBOARD_L1); CHECK(v7_hello(&k,p[0],"L1",&before,&taps) && !taps);
    /* Not ONLINE: K2 short toggles the route without a tap (the host page lags) ... */
    knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4);
    CHECK(!ux_count(f,ux_frames(p[0],f,ids,64),"\"col\":103,"));
    /* ... the next hello announces it first (one tap = down + up), then names L4 */
    CHECK(v7_hello(&k,p[0],"L4",&before,&taps) && taps==2 && before==2);
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); CHECK(onboard_health(&k)==HOST_ONLINE);
    CHECK(!ux_count(f,ux_frames(p[0],f,ids,64),"\"col\":103,")); /* no second tap at ONLINE */
    /* a change while PROBING (after the hello) is still announced at ONLINE */
    CHECK(v7_hello(&k,p[0],"L4",&before,&taps) && !taps);
    knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L1);
    CHECK(!ux_count(f,ux_frames(p[0],f,ids,64),"\"col\":103,"));
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); CHECK(onboard_health(&k)==HOST_ONLINE);
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"col\":103,")==2); }
    CHECK(v7_hello(&k,p[0],"L1",&before,&taps) && !taps);
    /* own pages: never a tap (host page left as it was), entering L4 later taps as before */
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); ux_frames(p[0],f,ids,64);
    onboard_route(&k,ONBOARD_HOME); CHECK(v7_hello(&k,p[0],"none",&before,&taps) && !taps);
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); ux_frames(p[0],f,ids,64);
    key(&k,8,1); key(&k,8,0); CHECK(onboard_current(&k)==ONBOARD_L4);
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"col\":103,")==2); }
    /* the ui.page reply names the page too */
    hk_fresh(&k); ux_frames(p[0],f,ids,64);
    CHECK(v7_page(&k,p[0],"L4",NULL)==1 && v7_page(&k,p[0],"L1",NULL)==1 && onboard_current(&k)==ONBOARD_L1);
    close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
}
/* C: a fresh hello starts both generations at 0; within a session they still strictly increase; requests
 * queued behind the hello are stale (refused, nothing consumed). */
static void v7_generation_reset(void)
{
    char f[64][192]; uint32_t ids[64];
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":999999999,\"keys\":{\"1\":\"1\"}}",NULL,0)==1);
    json(&k,"{\"method\":\"ui.page\",\"page\":\"L1\",\"generation\":999999999}");
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"result\":{\"page\":\"L1\",\"generation\":999999999}")==1); }
    hk_fresh(&k); ux_frames(p[0],f,ids,64);
    /* exhausted within this session */
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":999999999,\"keys\":{}}",NULL,0)==0);
    json(&k,"{\"method\":\"ui.page\",\"page\":\"L1\",\"generation\":999999999}");
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"ack_method\":\"ui.page\",\"success\":false")==1); }
    /* hello: both reported as 0 */
    json(&k,"{\"method\":\"ui.hello\",\"version\":1}");
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"page\":\"L1\",\"generation\":0,\"hid_generation\":0}}")==1); }
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":1,\"keys\":{}}",NULL,0)==1); /* PROBING: clear ok */
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":1,\"keys\":{}}",NULL,0)==0); /* still increasing */
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); hk_fresh(&k); ux_frames(p[0],f,ids,64);
    json(&k,"{\"method\":\"ui.page\",\"page\":\"L1\",\"generation\":1}");
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"result\":{\"page\":\"L1\",\"generation\":1}")==1); }
    json(&k,"{\"method\":\"ui.page\",\"page\":\"L4\",\"generation\":1}");
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"ack_method\":\"ui.page\",\"success\":false")==1); }
    CHECK(onboard_current(&k)==ONBOARD_L1);
    hk_fresh(&k);
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":2,\"keys\":{\"1\":\"1\"}}",NULL,0)==1);
    CHECK(hk_digit_ok(&k,hp[0],p[0],1,0x1E));
    /* Bytes already queued behind a hello (here: the same read) are stale: refused, generation not consumed. */
    { const char *hs="{\"method\":\"ui.hello\",\"version\":1}", *cl="{\"method\":\"ui.hidKeys\",\"generation\":7,\"keys\":{}}";
      uint8_t wire[512]; int a=host_encode(61,101,(const uint8_t *)hs,strlen(hs),wire,sizeof wire);
      int b=host_encode(62,101,(const uint8_t *)cl,strlen(cl),wire+a,sizeof wire-(size_t)a); CHECK(a>0 && b>0);
      kbd_host_feed(&k,wire,(size_t)(a+b));
      int n=ux_frames(p[0],f,ids,64);
      CHECK(ux_count(f,n,"\"ack_method\":\"ui.hello\",\"success\":true")==1);
      CHECK(ux_count(f,n,"\"ack_method\":\"ui.hidKeys\",\"success\":false,\"errorString\":\"stale UI request\"")==1); }
    CHECK(hk_raw(&k,p[0],"{\"method\":\"ui.hidKeys\",\"generation\":7,\"keys\":{}}",NULL,0)==1);
    /* a stale ui.page behind a hello likewise (and it would also need ONLINE) */
    { const char *hs="{\"method\":\"ui.hello\",\"version\":1}", *pg="{\"method\":\"ui.page\",\"page\":\"L4\",\"generation\":3}";
      uint8_t wire[512]; int a=host_encode(63,101,(const uint8_t *)hs,strlen(hs),wire,sizeof wire);
      int b=host_encode(64,101,(const uint8_t *)pg,strlen(pg),wire+a,sizeof wire-(size_t)a); CHECK(a>0 && b>0);
      kbd_host_feed(&k,wire,(size_t)(a+b));
      int n=ux_frames(p[0],f,ids,64);
      CHECK(ux_count(f,n,"\"ack_method\":\"ui.page\",\"success\":false,\"errorString\":\"stale UI request\"")==1); }
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); ux_frames(p[0],f,ids,64);
    json(&k,"{\"method\":\"ui.page\",\"page\":\"L4\",\"generation\":3}");
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"result\":{\"page\":\"L4\",\"generation\":3}")==1); }
    /* after a host loss the next hello starts from 0 as well */
    onboard_loss(&k); json(&k,"{\"method\":\"getInfo\"}"); ux_frames(p[0],f,ids,64);
    json(&k,"{\"method\":\"ui.hello\",\"version\":1}");
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"page\":\"L4\",\"generation\":0,\"hid_generation\":0}}")==1); }
    l1_teardown(&k,&c,hp,p);
}
/* D: LS fb17 会话安排 -> exactly {"method":"ui.openArrange"}; ONLINE only; own 3 s limiter. */
static void v7_open_arrange(void)
{
    char f[64][192]; uint32_t ids[64]; int n;
    static const char want[]="{\"method\":\"ui.openArrange\"}", settings[]="{\"method\":\"ui.openSettings\"}";
    kbd_t k; ctl_t c; init(&k,&c);
    int p[2]; CHECK(!pipe(p)); CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK)); k.host_fd=p[1];
    key(&k,5,1); key(&k,5,0); CHECK(onboard_current(&k)==ONBOARD_LS);
    /* Not ONLINE: nothing sent, the toast says so; LS stays. */
    key(&k,17,1); key(&k,17,0); n=ux_frames(p[0],f,ids,64);
    CHECK(!n && onboard_ui(&k)->toast==UI_SUB_ARRANGE_OFFLINE && onboard_current(&k)==ONBOARD_LS);
    ux_online_home(&k); CHECK(onboard_health(&k)==HOST_ONLINE && onboard_current(&k)==ONBOARD_LS); ux_frames(p[0],f,ids,64);
    key(&k,17,1); key(&k,17,0); n=ux_frames(p[0],f,ids,64);
    CHECK(n==1 && !strcmp(f[0],want) && ids[0]==KBD_EVENT_ID && onboard_ui(&k)->toast==UI_SUB_ARRANGE_OPENED);
    CHECK(onboard_current(&k)==ONBOARD_LS);
    /* rate limit: 3 s from the last attempt; the settings key has its own limiter */
    now+=1000; beat(&k); now+=1000; beat(&k); now+=999; beat(&k); ux_frames(p[0],f,ids,64);
    key(&k,17,1); key(&k,17,0); n=ux_frames(p[0],f,ids,64); CHECK(!n);
    key(&k,19,1); key(&k,19,0); n=ux_frames(p[0],f,ids,64); CHECK(n==1 && !strcmp(f[0],settings));
    now+=1; key(&k,17,1); key(&k,17,0); n=ux_frames(p[0],f,ids,64); CHECK(n==1 && !strcmp(f[0],want));
    /* No other LS key, no HOME key and no host-page key ever sends it. */
    now+=3000; beat(&k); ux_frames(p[0],f,ids,64);
    for (int fb=1;fb<=15;fb++) { key(&k,fb,1); key(&k,fb,0); }
    n=ux_frames(p[0],f,ids,64); CHECK(!ux_count(f,n,"openArrange") && onboard_current(&k)==ONBOARD_LS);
    key(&k,18,1); key(&k,18,0); CHECK(onboard_current(&k)==ONBOARD_HOME);
    now+=3000; beat(&k); ux_frames(p[0],f,ids,64);
    for (int fb=9;fb<=19;fb++) if (fb!=15) { key(&k,fb,1); key(&k,fb,0); }
    n=ux_frames(p[0],f,ids,64); CHECK(!ux_count(f,n,"openArrange") && onboard_current(&k)==ONBOARD_HOME);
    close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
    /* live L1/L4: fb17 is the Mac's Backspace, never ui.openArrange */
    hid_t h; int hp[2]; l1_setup(&k,&c,&h,hp,p);
    key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0)); key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    n=ux_frames(p[0],f,ids,64); CHECK(!ux_count(f,n,"openArrange") && !ux_count(f,n,"keyStateChanged"));
    /* K1 short on L4 -> LS, fb17 there -> the event */
    knob(&k,103,1); now+=20; knob(&k,103,0); hk_fresh(&k); knob(&k,100,1); now+=20; knob(&k,100,0);
    CHECK(onboard_current(&k)==ONBOARD_LS); ux_frames(p[0],f,ids,64);
    key(&k,17,1); key(&k,17,0); n=ux_frames(p[0],f,ids,64); CHECK(n==1 && !strcmp(f[0],want) && l1_none(hp[0]));
    l1_teardown(&k,&c,hp,p);
}
/* Kimi K3 r1 (1.6.0): (1) a key still owned from before an e-stop keeps that owner through a repeated GD32
 * down in the post-e-stop pause (its host up is due); (2) L4 回 L1 is inert while suspended; (3) a key held
 * across a same-page ui.page whose GD32 up is lost ends (host key / L4 back: 10 s after the request; PTT: its
 * 120 s cap, which every PTT hold now has). */
static void v7_estop_pause_owners(void)
{
    char f[64][192]; uint32_t ids[64]; int ups;
    for (int same=0;same<2;same++) {
        kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
        CHECK(hk_map(&k,p[0],HK3)==1);
        key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0));      /* forwarded Backspace (owner 6) */
        key(&k,12,1);                                       /* plain host key (owner 1) */
        { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"pressed\":true")==2); }
        if (same) CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups);
        req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped);
        CHECK(l1_one(hp[0],0,0,0,0) && !h.keys[0]);          /* the stop released the HID half */
        CHECK(!v7_ups(p[0]));                                /* host halves still due */
        key(&k,17,1); key(&k,12,1);                          /* repeated GD32 downs in the pause */
        CHECK(l1_none(hp[0]) && !l1_host_keys(p[0]));
        key(&k,17,0); key(&k,12,0);
        CHECK(l1_none(hp[0]) && v7_ups(p[0])==2);            /* both host ups delivered */
        live(&k,1000); if (same) hk_fresh(&k);
        key(&k,18,1); CHECK(l1_one(hp[0],0,0x28,0,0)); key(&k,18,0); CHECK(l1_one(hp[0],0,0,0,0));
        l1_teardown(&k,&c,hp,p);
    }
    /* (2) L4 回 L1 held when the e-stop comes: its release never navigates nor taps the host. */
    for (int same=0;same<2;same++) {
        kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
        knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); hk_fresh(&k); ux_frames(p[0],f,ids,64);
        key(&k,5,1);
        if (same) CHECK(v7_page(&k,p[0],"L4",&ups)==1 && !ups);
        req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped);
        ux_frames(p[0],f,ids,64);
        key(&k,5,0);
        { int n=ux_frames(p[0],f,ids,64); CHECK(!ux_count(f,n,"\"col\":103,") && !ux_count(f,n,"keyStateChanged")); }
        CHECK(onboard_current(&k)==ONBOARD_L4);
        live(&k,1000); if (same) hk_fresh(&k); ux_frames(p[0],f,ids,64);
        key(&k,5,1); key(&k,5,0); CHECK(onboard_current(&k)==ONBOARD_L1); /* after the pause: works again */
        { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"col\":103,")==2); }
        l1_teardown(&k,&c,hp,p);
    }
}
#define PTT_CAP_MS 120000 /* onboard.c HID_PTT_HOLD_MS */
static void v7_same_page_lost_up(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p); int ups;
    char f[64][192]; uint32_t ids[64];
    /* PTT + a host key held, same-page request, both GD32 ups lost. The host key (no bound of its own) ends
     * 10 s after the request; the PTT (dictation: never the 10 s cut) ends at its 120 s cap from the press. */
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); key(&k,12,1); l1_host_keys(p[0]);
    CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups);
    hk_fresh(&k);
    pump(&k); int t=onboard_timeout(&k,now,600000); CHECK(t>0 && t<=L1_HOLD_MS); /* the loop wakes for the bound */
    live(&k,8000); CHECK(l1_none(hp[0]) && h.mods==HID_MOD_RALT && !v7_ups(p[0]));
    live(&k,0); CHECK(l1_none(hp[0]) && h.mods==HID_MOD_RALT && v7_ups(p[0])==1);   /* 10 s: host up, PTT kept */
    pump(&k); t=onboard_timeout(&k,now,600000); CHECK(t>0 && t<=PTT_CAP_MS);
    live(&k,PTT_CAP_MS-14000); CHECK(l1_none(hp[0]) && h.mods==HID_MOD_RALT);       /* ~118 s */
    live(&k,2000); CHECK(l1_one(hp[0],0,0,0,0) && !h.mods);                          /* 120 s: RAlt up */
    key(&k,16,0); key(&k,12,0); CHECK(l1_none(hp[0]) && !v7_ups(p[0]));             /* the late ups: nothing */
    /* The cap applies to every PTT hold (not only across same-page requests); a repeated GD32 down of the
     * held PTT confirms it pressed and restarts the window (also while the page waits for its frame). */
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0));
    live(&k,100000); key(&k,16,1); CHECK(l1_none(hp[0]));                           /* re-confirmed at ~101 s */
    ux_frames(p[0],f,ids,64); /* (100 heartbeat acks) */
    CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups);
    live(&k,60000); key(&k,16,1); CHECK(l1_none(hp[0]));                            /* not live yet: still kept */
    hk_fresh(&k); live(&k,100000); CHECK(l1_none(hp[0]) && h.mods==HID_MOD_RALT);  /* 4 min of dictation */
    key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0));
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); live(&k,PTT_CAP_MS); CHECK(l1_one(hp[0],0,0,0,0)); /* no same-page */
    key(&k,16,0); CHECK(l1_none(hp[0]));
    key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0)); /* press again: works */
    /* Repeated same-page requests never extend a host key's bound; a GD32 down re-confirms it. */
    ux_frames(p[0],f,ids,64); key(&k,12,1); l1_host_keys(p[0]);
    CHECK(v7_page(&k,p[0],"L1",&ups)==1); hk_fresh(&k); live(&k,5000);
    CHECK(v7_page(&k,p[0],"L1",&ups)==1); hk_fresh(&k); live(&k,3000); CHECK(v7_ups(p[0])==1);
    key(&k,12,0); CHECK(!v7_ups(p[0]));
    /* A pending L4 回 L1 kept across a same-page request is cancelled by the bound, never run late. */
    knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); hk_fresh(&k); ux_frames(p[0],f,ids,64);
    key(&k,5,1); CHECK(v7_page(&k,p[0],"L4",&ups)==1); hk_fresh(&k);
    live(&k,10000); key(&k,5,0); CHECK(onboard_current(&k)==ONBOARD_L4);
    { int n=ux_frames(p[0],f,ids,64); CHECK(!ux_count(f,n,"\"col\":103,")); }
    key(&k,5,1); key(&k,5,0); CHECK(onboard_current(&k)==ONBOARD_L1); /* a fresh press works */
    l1_teardown(&k,&c,hp,p);
}
/* Host key events since the last read, per logical key. keyStateChanged is a state: downs[] = 1 while the host
 * sees the key pressed (a repeated down of a held host key is forwarded again: still pressed), an up while the
 * host already sees it released counts as bad; ups[] counts the ups. */
static void v7_count(int fd, int downs[20], int ups[20], int *bad)
{
    char f[64][192]; uint32_t ids[64]; int n=ux_frames(fd,f,ids,64);
    for (int i=0;i<n;i++) {
        const char *q=strstr(f[i],"\"col\":"), *r=strstr(f[i],"\"row\":");
        if (!strstr(f[i],"keyStateChanged") || !q || !r) continue;
        int col=atoi(q+6), row=atoi(r+6);
        if (col<0 || col>4 || row<0 || row>3) continue; /* knob taps */
        int fb=row*5+col;
        if (strstr(f[i],"\"pressed\":true")) downs[fb]=1;
        else { ups[fb]++; if (!downs[fb]) (*bad)++; downs[fb]=0; }
    }
}
/* Randomized: PTT / Mac HID keys / host keys / MODE / L4 back pressed, repeated and released in any order
 * across same-page and real ui.page, K2, socket e-stops, option maps, fresh frames and time. Every physical
 * key is released at the end (no lost ups here): HID must end all-up, the host must see every key released
 * and never an up for a key it sees released. */
static void v7_fuzz_release_invariant(void)
{
    static const int keys[] = {1,2,5,12,16,17,18,19,20};
    unsigned seed = 0x16f0u;
    for (int run=0;run<120;run++) {
        kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
        int phys[21]={0}, downs[20]={0}, upsk[20]={0}, bad=0;
#define RND(n) ((seed = seed*1103515245u + 12345u) >> 16) % (n)
        for (int step=0;step<45;step++) {
            int op=(int)RND(100), fb=keys[RND(sizeof keys/sizeof *keys)];
            enum onboard_route r=onboard_current(&k);
            if (op<30) { if (!phys[fb] || RND(4)==0) { phys[fb]=1; key(&k,fb,1); } }
            else if (op<55) { if (phys[fb]) { phys[fb]=0; key(&k,fb,0); } }
            else if (op<67) { /* same page (mostly) or the other one; json() directly: every frame is counted */
                char q[160]; int same=op<63; const char *pg=(r==ONBOARD_L1)==same ? "L1" : "L4";
                snprintf(q,sizeof q,"{\"method\":\"ui.page\",\"page\":\"%s\",\"generation\":%ld}",pg,++v7_gen); json(&k,q); }
            else if (op<71) { knob(&k,103,1); now+=20; knob(&k,103,0); }
            else if (op<74) { req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); }
            else if (op<79 && r==ONBOARD_L1) {
                char q[160]; snprintf(q,sizeof q,"{\"method\":\"ui.hidKeys\",\"generation\":%ld,\"keys\":{%s}}",++hk_gen,HK3); json(&k,q); }
            else if (op<87) { beat(&k); if (r==ONBOARD_L1 || r==ONBOARD_L4) l1_fresh(&k); beat(&k); }
            else live(&k,(int)RND(4)*1000);
            if (onboard_current(&k)!=ONBOARD_L1 && onboard_current(&k)!=ONBOARD_L4) onboard_route(&k,ONBOARD_L1);
            v7_count(p[0],downs,upsk,&bad);
        }
        for (int i=0;i<(int)(sizeof keys/sizeof *keys);i++) if (phys[keys[i]]) { phys[keys[i]]=0; key(&k,keys[i],0); }
        live(&k,12000); v7_count(p[0],downs,upsk,&bad);
        { uint8_t r[16][9]; l1_reports(hp[0],r,16); }
        int ok = !bad && !h.mods && !h.keys[0] && !h.keys[1] && !h.keys[2] && !h.keys[3];
        for (int i=0;i<20;i++) ok &= !downs[i]; /* the host sees every key released */
        CHECK(ok);
        if (!ok) { fprintf(stderr,"v7_fuzz: run %d mods=%d keys=%d,%d,%d bad=%d\n",run,h.mods,h.keys[0],h.keys[1],h.keys[2],bad);
                   for (int i=0;i<20;i++) if (downs[i]) fprintf(stderr,"  fb%d still pressed on the host (%d ups)\n",i+1,upsk[i]); }
#undef RND
        l1_teardown(&k,&c,hp,p);
    }
}
/* rc3 (Kimi K3 delta review): a faulty GD32 emitting phantom downs renews the PTT's 120 s window and a kept key's
 * 10 s window, but never past the absolute caps: PTT 10 min from the original press, a kept host key / L4 回 L1
 * 60 s from the first same-page request. */
#define PTT_MAX_MS 600000 /* onboard.c HID_PTT_MAX_MS */
#define KEEP_MAX 60000    /* onboard.c KEEP_MAX_MS */
static void v7_phantom_down_storm(void)
{
    char f[64][192]; uint32_t ids[64]; int ups;
    for (int same=0;same<2;same++) { /* live page / a same-page request with the page waiting for its frame */
        kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
        key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0));
        int64_t t0=now, cut=-1;
        if (same) CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups); /* (never fresh again: guard path) */
        for (int sec=1;sec<=PTT_MAX_MS/1000+30 && cut<0;sec++) {
            now+=1000; beat(&k); key(&k,16,1); /* a phantom down every second */
            for (int i=0;i<8;i++) kbd_service(&k,now);
            if (!(sec%30)) ux_frames(p[0],f,ids,64);
            if (!h.mods) cut=now-t0;
        }
        CHECK(cut>=PTT_MAX_MS && cut<=PTT_MAX_MS+1000); /* not at 120 s (renewed), at 10 min: absolute */
        { uint8_t r[16][9]; int n=l1_reports(hp[0],r,16); CHECK(n==1 && l1_is(r[0],0,0,0,0)); }
        CHECK(onboard_key_owner(&k,16)==0);
        for (int sec=0;sec<30;sec++) { now+=1000; beat(&k); key(&k,16,1); } /* the storm goes on: stays up */
        CHECK(l1_none(hp[0]) && !h.mods && onboard_key_owner(&k,16)==0);
        key(&k,16,0); CHECK(l1_none(hp[0]));                                 /* its up: an ordinary key again */
        if (same) hk_fresh(&k);
        key(&k,16,1); CHECK(l1_one(hp[0],HID_MOD_RALT,0,0,0)); key(&k,16,0); CHECK(l1_one(hp[0],0,0,0,0));
        l1_teardown(&k,&c,hp,p);
    }
    /* kept host key / L4 回 L1 across a same-page request, phantom downs every 5 s */
    for (int back=0;back<2;back++) {
        kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
        int fb = back ? 5 : 12;
        if (back) { knob(&k,103,1); now+=20; knob(&k,103,0); CHECK(onboard_current(&k)==ONBOARD_L4); hk_fresh(&k); }
        ux_frames(p[0],f,ids,64);
        key(&k,fb,1); ux_frames(p[0],f,ids,64);
        CHECK(v7_page(&k,p[0],back ? "L4" : "L1",&ups)==1 && !ups); hk_fresh(&k);
        int64_t t0=now, cut=-1;
        for (int sec=1;sec<=KEEP_MAX/1000+20 && cut<0;sec++) {
            now+=1000; beat(&k); if (!(sec%5)) key(&k,fb,1);
            for (int i=0;i<8;i++) kbd_service(&k,now);
            int n=ux_frames(p[0],f,ids,64);
            if (!back && ux_count(f,n,"\"pressed\":false")) cut=now-t0;
            if (back && !onboard_key_owner(&k,fb)) cut=now-t0;
        }
        CHECK(cut>=KEEP_MAX && cut<=KEEP_MAX+1000); /* renewed past 10 s, ended at 60 s */
        for (int sec=0;sec<10;sec++) { now+=1000; beat(&k); key(&k,fb,1); } /* more phantom downs: inert */
        CHECK(!onboard_key_owner(&k,fb) && !v7_ups(p[0]) && !l1_host_keys(p[0]));
        key(&k,fb,0); CHECK(onboard_current(&k)==(back ? ONBOARD_L4 : ONBOARD_L1) && !v7_ups(p[0]));
        key(&k,fb,1); key(&k,fb,0); /* after its up: works again */
        if (back) CHECK(onboard_current(&k)==ONBOARD_L1); else CHECK(v7_ups(p[0])==1);
        l1_teardown(&k,&c,hp,p);
    }
}
/* rc3: after any lost key-up guard cut a key is inert until the GD32 reports its up: a Backspace phantom-down
 * storm types one 10 s Backspace, not one every 10 s; a real user (up before the next press) sees no change. */
static void v7_cut_latch(void)
{
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0));
    for (int sec=1;sec<=30;sec++) { now+=1000; beat(&k); key(&k,17,1); for (int i=0;i<8;i++) kbd_service(&k,now); }
    { uint8_t r[16][9]; int n=l1_reports(hp[0],r,16); CHECK(n==1 && l1_is(r[0],0,0,0,0)); } /* one cut, no re-press */
    key(&k,17,0); CHECK(l1_none(hp[0]));
    key(&k,17,1); CHECK(l1_one(hp[0],0,0x2A,0,0)); key(&k,17,0); CHECK(l1_one(hp[0],0,0,0,0));
    /* fb20 as the e-stop is never latched away: a cut Mac mode key still e-stops once the agent is active */
    key(&k,20,1); l1_host_keys(p[0]);
    CHECK(v7_page(&k,p[0],"L1",NULL)==1); hk_fresh(&k); live(&k,9000); CHECK(v7_ups(p[0])==1); /* kept-key bound */
    ctl_set_allowed(&c,1); live(&k,0);
    key(&k,20,1); CHECK(c.estopped);
    l1_teardown(&k,&c,hp,p);
}
/* rc3 (pre-existing in 1.5.1): the 5 s resync of the post-e-stop pause also releases the press-time owners left
 * by the stop (owner 1: host keys and the host halves of forwarded keys / digits), whose GD32 ups were lost. */
static void v7_pause_resync_releases_owners(void)
{
    char f[64][192]; uint32_t ids[64];
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,17,1); key(&k,1,1); key(&k,16,1); key(&k,12,1); /* the digit before the PTT (held PTT: ignored) */
    CHECK(onboard_key_owner(&k,16)==2 && onboard_key_owner(&k,17)==6 && onboard_key_owner(&k,1)==7 && onboard_key_owner(&k,12)==1);
    { uint8_t r[16][9]; l1_reports(hp[0],r,16); } ux_frames(p[0],f,ids,64);
    req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped);
    { uint8_t r[16][9]; int n=l1_reports(hp[0],r,16); CHECK(n>=1 && l1_is(r[n-1],0,0,0,0) && !h.mods && !h.keys[0]); }
    /* after the stop: 2 and 5 released, 6/7 only their host halves (1) remain, nothing else */
    CHECK(onboard_key_owner(&k,16)==0 && onboard_key_owner(&k,17)==1 && onboard_key_owner(&k,1)==1 && onboard_key_owner(&k,12)==1);
    for (int fb=1;fb<=20;fb++) if (fb!=17 && fb!=1 && fb!=12) CHECK(onboard_key_owner(&k,fb)==0);
    CHECK(!v7_ups(p[0]));
    /* all three GD32 ups lost: 5 s without input -> their host ups, owners clear, the pause can end */
    live(&k,3000); CHECK(!v7_ups(p[0]));
    live(&k,1000); CHECK(v7_ups(p[0])==3);
    for (int fb=1;fb<=20;fb++) CHECK(onboard_key_owner(&k,fb)==0);
    CHECK(l1_none(hp[0]));
    live(&k,1000); ux_frames(p[0],f,ids,64);
    key(&k,17,1); key(&k,16,0); key(&k,1,0); key(&k,12,0); CHECK(!v7_ups(p[0])); /* late physical ups: nothing */
    key(&k,17,0); CHECK(l1_reports(hp[0],(uint8_t (*)[9])f,16)==2); /* the pause is over: Backspace down + up */
    l1_teardown(&k,&c,hp,p);
    /* L4 回 L1: cancelled by the stop (owner 4 -> 0), the resync has nothing left */
    l1_setup(&k,&c,&h,hp,p);
    knob(&k,103,1); now+=20; knob(&k,103,0); hk_fresh(&k); ux_frames(p[0],f,ids,64);
    key(&k,5,1); CHECK(onboard_key_owner(&k,5)==4);
    req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(onboard_key_owner(&k,5)==0);
    live(&k,6000); key(&k,5,0); CHECK(onboard_current(&k)==ONBOARD_L4);
    { int n=ux_frames(p[0],f,ids,64); CHECK(!ux_count(f,n,"\"col\":103,")); }
    l1_teardown(&k,&c,hp,p);
}
/* rc4 (Kimi K3 rc3 delta): (1) every place that ends an owner also ends its same-page bound — a leftover keep
 * pair cut a later press of the same key early and latched it; (2) a repeated GD32 down of a held host key is
 * the same press (not forwarded again: a phantom storm cannot flood the Mac), the next up releases, a genuine
 * long press is one down + one up; (3) the keep-cut log line names the real held time. */
static int v7_stderr_begin(FILE **t) { fflush(stderr); int saved=dup(2); *t=tmpfile(); CHECK(*t!=NULL); dup2(fileno(*t),2); return saved; }
static void v7_stderr_end(int saved, FILE *t, char *out, size_t cap)
{ fflush(stderr); dup2(saved,2); close(saved); rewind(t); size_t n=fread(out,1,cap-1,t); out[n]=0; fclose(t); }
static void v7_keep_pair_cleared(void)
{
    char f[64][192]; uint32_t ids[64]; int ups;
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    key(&k,12,1); l1_host_keys(p[0]);
    CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups); /* keep armed at T */
    int saved=k.host_fd; k.host_fd=-1; onboard_host_flush(&k); k.host_fd=saved; /* host gone: owner 1 dropped */
    CHECK(onboard_key_owner(&k,12)==0);
    hk_fresh(&k); live(&k,2000); ux_frames(p[0],f,ids,64); /* its GD32 up lost meanwhile */
    key(&k,12,1); { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"pressed\":true")==1); } /* a new press */
    live(&k,12000); CHECK(!v7_ups(p[0]) && onboard_key_owner(&k,12)==1); /* T+10 s passed: not cut, not latched */
    key(&k,12,0); CHECK(v7_ups(p[0])==1);
    key(&k,12,1); key(&k,12,0); CHECK(v7_ups(p[0])==1);
    /* a pending up flushed by the transport also clears it: re-arming after a new press works */
    key(&k,12,1); l1_host_keys(p[0]);
    CHECK(v7_page(&k,p[0],"L1",&ups)==1); hk_fresh(&k); live(&k,9000); CHECK(v7_ups(p[0])==1); /* bound re-armed */
    key(&k,12,0); CHECK(!v7_ups(p[0]));
    l1_teardown(&k,&c,hp,p);
}
static void v7_host_key_repeated_down(void)
{
    char f[64][192]; uint32_t ids[64];
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    key(&k,12,1);
    for (int i=0;i<50;i++) { now+=100; if (!(i%10)) beat(&k); key(&k,12,1); } /* a phantom-down storm, live page */
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"keyStateChanged")==1 && ux_count(f,n,"\"pressed\":true")==1); }
    CHECK(onboard_key_owner(&k,12)==1);
    key(&k,12,0); CHECK(v7_ups(p[0])==1 && !onboard_key_owner(&k,12)); /* the next up releases */
    /* a genuine long press (the Mac's 600 ms pick-up) is one down + one up, unchanged */
    key(&k,3,1); now+=800; beat(&k); key(&k,3,0);
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"keyStateChanged")==2 && ux_count(f,n,"\"pressed\":true")==1); }
    /* the PTT-ignored digit (owner 1) and the Mac mode key (owner 1) likewise */
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,16,1); key(&k,1,1); key(&k,1,1); key(&k,1,1); key(&k,20,1); key(&k,20,1);
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"ignored\":\"ptt\"")==1 && ux_count(f,n,"\"pressed\":true")==2); }
    key(&k,1,0); key(&k,20,0); key(&k,16,0); CHECK(v7_ups(p[0])==2);
    /* the keep-cut log line names the real held time (here ~60 s: phantom downs every 5 s) */
    key(&k,12,1); l1_host_keys(p[0]);
    CHECK(v7_page(&k,p[0],"L1",NULL)==1); hk_fresh(&k);
    FILE *t; int saved=v7_stderr_begin(&t);
    for (int sec=1;sec<=KEEP_MAX/1000+2;sec++) { now+=1000; beat(&k); if (!(sec%5)) key(&k,12,1); for (int i=0;i<8;i++) kbd_service(&k,now); }
    char log[4096]; v7_stderr_end(saved,t,log,sizeof log);
    CHECK(strstr(log,"key held 60 s across a same-page ui.page") || strstr(log,"key held 61 s across a same-page ui.page"));
    CHECK(v7_ups(p[0])==1);
    l1_teardown(&k,&c,hp,p);
}
static void round7_regressions(void)
{
    v7_same_page_keeps_held(); v7_same_page_option_mode(); v7_same_page_release_paths(); v7_hello_page();
    v7_generation_reset(); v7_open_arrange(); v7_estop_pause_owners(); v7_same_page_lost_up(); v7_fuzz_release_invariant();
    v7_phantom_down_storm(); v7_cut_latch(); v7_pause_resync_releases_owners(); v7_keep_pair_cleared();
    v7_host_key_repeated_down();
}
