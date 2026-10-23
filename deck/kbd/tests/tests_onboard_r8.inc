/* 1.7.0: a held host key (owner 1) whose GD32 up is lost and that no same-page request bounded is ended 60 s after
 * its press like the other lost key-up guards: the host gets its up, the key is inert (cut latch) until the GD32
 * reports the up. Covers a session key, the Mac mode key fb20, a slot cut while another key came and went, the
 * PTT-ignored digit (owner 1 from its press) and a forwarded fb17 whose host half (owner 1) outlives its 10 s HID
 * cut. A genuine press (one down + one up, the Mac's 600 ms long press included) and holds up to 59 s are unchanged.
 * Fake clock, nonblocking pipes; no device paths. */
#define HOST_KEY_MAX 60000 /* onboard.c HOST_KEY_MAX_MS */
/* Keep fb held (pressed at t0) with its GD32 up lost - phantom downs every 5 s if asked - until the host sees an up;
 * returns the hold time at that up (-1: none within limit_s) and the stderr text in log. */
static int64_t v8_hold_until_up(kbd_t *k, int fd, int fb, int64_t t0, int limit_s, int phantom, char *log, size_t cap)
{
    char f[64][192]; uint32_t ids[64]; int64_t cut=-1;
    FILE *t; int saved=v7_stderr_begin(&t);
    for (int sec=1;sec<=limit_s && cut<0;sec++) {
        now+=1000; beat(k); if (phantom && !(sec%5)) key(k,fb,1);
        for (int i=0;i<8;i++) kbd_service(k,now);
        int n=ux_frames(fd,f,ids,64);
        if (ux_count(f,n,"\"pressed\":false")) cut=now-t0;
    }
    v7_stderr_end(saved,t,log,cap);
    return cut;
}
static void v8_host_key_cap(void)
{
    char f[64][192]; uint32_t ids[64]; char text[4096];
    const int fbs[3]={12,20,3};   /* a session key, the Mac mode key fb20, a slot cut while another key came and went */
    for (int c=0;c<3;c++) {
        kbd_t k; ctl_t ct; hid_t h; int hp[2], p[2]; l1_setup(&k,&ct,&h,hp,p);
        int fb=fbs[c];
        key(&k,fb,1); { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"pressed\":true")==1); }
        CHECK(onboard_key_owner(&k,fb)==1);
        int64_t t0=now;
        if (c==2) { /* another host key pressed and released normally 30 s into the hold */
            for (int sec=1;sec<=30;sec++) { now+=1000; beat(&k); for (int i=0;i<4;i++) kbd_service(&k,now); }
            key(&k,7,1); now+=200; key(&k,7,0);
            { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"pressed\":true")==1 && ux_count(f,n,"\"pressed\":false")==1); }
        }
        int64_t cut=v8_hold_until_up(&k,p[0],fb,t0,HOST_KEY_MAX/1000+20,1,text,sizeof text);
        CHECK(cut>=HOST_KEY_MAX && cut<=HOST_KEY_MAX+1000); /* not earlier, not renewed by the phantom downs */
        CHECK(strstr(text,"host key held 60 s: released (lost key-up guard)")!=NULL);
        CHECK(onboard_key_owner(&k,fb)==0);
        for (int sec=0;sec<10;sec++) { now+=1000; beat(&k); key(&k,fb,1); for (int i=0;i<4;i++) kbd_service(&k,now); }
        CHECK(!l1_host_keys(p[0]) && onboard_key_owner(&k,fb)==0);       /* cut latch: phantom downs are inert */
        key(&k,fb,0); CHECK(!l1_host_keys(p[0]));                          /* its late up: nothing */
        key(&k,fb,1); key(&k,fb,0);                                        /* an ordinary key again */
        { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"pressed\":true")==1 && ux_count(f,n,"\"pressed\":false")==1); }
        l1_teardown(&k,&ct,hp,p);
    }
}
/* Holds shorter than the cap and the Mac's long press are untouched; a same-page bound and the press cap end a kept
 * key at whichever comes first, once. */
static void v8_host_key_cap_bounds(void)
{
    char f[64][192]; uint32_t ids[64]; char text[4096]; int ups;
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    /* 59 s held, then the real up: one up, at the release */
    key(&k,12,1); ux_frames(p[0],f,ids,64);
    for (int sec=1;sec<=59;sec++) { now+=1000; beat(&k); for (int i=0;i<8;i++) kbd_service(&k,now); }
    CHECK(!v7_ups(p[0]) && onboard_key_owner(&k,12)==1);
    key(&k,12,0); CHECK(v7_ups(p[0])==1 && onboard_key_owner(&k,12)==0);
    /* the Mac's 600 ms long press: one down + one up, unchanged */
    key(&k,3,1); now+=800; beat(&k); key(&k,3,0);
    { int n=ux_frames(p[0],f,ids,64); CHECK(ux_count(f,n,"\"pressed\":true")==1 && ux_count(f,n,"\"pressed\":false")==1); }
    /* a same-page request 55 s into the hold: its 10 s bound would end at 65 s; the press cap ends it at 60 s, once */
    key(&k,12,1); l1_host_keys(p[0]); int64_t t0=now;
    for (int sec=1;sec<=55;sec++) { now+=1000; beat(&k); for (int i=0;i<4;i++) kbd_service(&k,now); }
    CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups); hk_fresh(&k);
    int64_t cut=v8_hold_until_up(&k,p[0],12,t0,20,0,text,sizeof text);
    CHECK(cut>=HOST_KEY_MAX && cut<=HOST_KEY_MAX+1000);
    CHECK(strstr(text,"host key held 60 s: released (lost key-up guard)")!=NULL && !strstr(text,"across a same-page"));
    live(&k,20000); CHECK(!v7_ups(p[0]));                                  /* never a second up */
    key(&k,12,0); key(&k,12,1); key(&k,12,0); CHECK(v7_ups(p[0])==1);
    /* a request right after the press: its 10 s bound ends it first (its own line), the cap never fires later */
    key(&k,12,1); l1_host_keys(p[0]); t0=now;
    CHECK(v7_page(&k,p[0],"L1",&ups)==1 && !ups); hk_fresh(&k);
    cut=v8_hold_until_up(&k,p[0],12,t0,70,0,text,sizeof text);
    CHECK(cut>=10000 && cut<=12000 && strstr(text,"across a same-page ui.page") && !strstr(text,"host key held 60 s"));
    live(&k,60000); CHECK(!v7_ups(p[0]));
    key(&k,12,0);
    l1_teardown(&k,&c,hp,p);
}
/* The PTT-ignored digit (owner 1 from its press), and a forwarded fb17 (option mode, owner 6) that an e-stop turned
 * into its host half (owner 1; release_mac keeps the original press) while phantom downs keep the post-e-stop pause
 * from resyncing: both end 60 s after the original press. */
static void v8_host_halves_capped(void)
{
    char f[64][192]; uint32_t ids[64]; char text[4096];
    kbd_t k; ctl_t c; hid_t h; int hp[2], p[2]; l1_setup(&k,&c,&h,hp,p);
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,16,1); key(&k,1,1); /* PTT held: the digit is ignored (forwarded marked), owner 1 */
    CHECK(onboard_key_owner(&k,1)==1);
    int64_t t0=now;
    key(&k,16,0); { uint8_t r[16][9]; l1_reports(hp[0],r,16); } ux_frames(p[0],f,ids,64);
    int64_t cut=v8_hold_until_up(&k,p[0],1,t0,HOST_KEY_MAX/1000+5,1,text,sizeof text);
    CHECK(cut>=HOST_KEY_MAX && cut<=HOST_KEY_MAX+1000 && onboard_key_owner(&k,1)==0);
    key(&k,1,0);
    l1_teardown(&k,&c,hp,p);
    l1_setup(&k,&c,&h,hp,p);
    CHECK(hk_map(&k,p[0],HK3)==1);
    key(&k,17,1); CHECK(onboard_key_owner(&k,17)==6); { uint8_t r[16][9]; CHECK(l1_reports(hp[0],r,16)==1); }
    ux_frames(p[0],f,ids,64); t0=now;
    now+=2000; beat(&k);
    req(&c,"{\"op\":\"estop\"}","true"); kbd_safety_tick(&k,now); CHECK(c.estopped);
    { uint8_t r[16][9]; int n=l1_reports(hp[0],r,16); CHECK(n>=1 && l1_is(r[n-1],0,0,0,0)); } /* HID released */
    CHECK(onboard_key_owner(&k,17)==1 && !v7_ups(p[0]));                                    /* host half kept */
    cut=-1;
    for (int sec=1;sec<=HOST_KEY_MAX/1000+5 && cut<0;sec++) {
        now+=1000; beat(&k); key(&k,17,1);       /* a phantom down every second: no 5 s quiet resync */
        for (int i=0;i<8;i++) kbd_service(&k,now);
        int n=ux_frames(p[0],f,ids,64);
        if (ux_count(f,n,"\"pressed\":false")) cut=now-t0;
    }
    CHECK(cut>=HOST_KEY_MAX && cut<=HOST_KEY_MAX+1000 && onboard_key_owner(&k,17)==0);
    CHECK(l1_none(hp[0]));                       /* no HID from the phantom downs */
    key(&k,17,0);
    l1_teardown(&k,&c,hp,p);
}
static void round8_regressions(void)
{
    v8_host_key_cap(); v8_host_key_cap_bounds(); v8_host_halves_capped();
}
