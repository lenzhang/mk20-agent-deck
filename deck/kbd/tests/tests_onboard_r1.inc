/* Round 1 regressions: fake clock, socketpairs/FIFOs and regular panel files only. */
static void read_report(int fd, int mod)
{
    uint8_t got[32]={0}, want[9]={1,0,0,0,0,0,0,0,0}; want[1]=(uint8_t)mod;
    CHECK(read(fd,got,sizeof got)==9); CHECK(!memcmp(got,want,9));
}
static int host_events(int fd, int *downs, int *ups)
{
    uint8_t buf[16384]; host_parser p; host_parser_init(&p,sizeof buf);
    ssize_t n; while ((n=read(fd,buf,sizeof buf))>0) host_push(&p,buf,(size_t)n);
    host_frame f; int count=0; *downs=*ups=0;
    while (host_next(&p,&f)) if (f.cmd==HOST_CMD_JSON) {
        char s[512]; size_t len=f.size<sizeof s-1 ? f.size : sizeof s-1;
        memcpy(s,f.data,len); s[len]=0;
        if (strstr(s,"keyStateChanged")) {
            count++; if (strstr(s,"true")) (*downs)++; else (*ups)++;
        }
    }
    host_parser_free(&p); return count;
}
static void r1_queued_hello_and_hid(void)
{
    kbd_t k; ctl_t c; init(&k,&c);
    int sv[2]; CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,sv)); k.host_fd=sv[0];
    fcntl(sv[0],F_SETFL,O_NONBLOCK); fcntl(sv[1],F_SETFL,O_NONBLOCK);
    const char *hello_json="{\"method\":\"ui.hello\",\"version\":1}";
    uint8_t wire[256]; int len=host_encode(1,101,(const uint8_t *)hello_json,strlen(hello_json),wire,sizeof wire);
    CHECK(write(sv[1],wire,(size_t)len)==len);
    key(&k,7,1); key(&k,7,0); /* hello is unread when the physical route changes */
    CHECK(onboard_current(&k)==ONBOARD_L1 && k.host_rx_barrier==(size_t)len);
    CHECK(read(sv[0],wire,sizeof wire)==len); kbd_host_feed(&k,wire,(size_t)len);
    CHECK(onboard_health(&k)==HOST_PROBING);
    beat(&k); now+=1000; beat(&k); now+=1000; beat(&k);
    CHECK(onboard_health(&k)==HOST_ONLINE); jpeg(&k); finish_decode(&k);
    CHECK(onboard_tile_allowed(&k,1,0,1));
    int downs,ups; host_events(sv[1],&downs,&ups);
    key(&k,1,1); knob(&k,105,1);
    uint8_t echo[]={5,0,103,103,0,0}; kbd_on_gd32(&k,echo,sizeof echo);
    CHECK(host_events(sv[1],&downs,&ups)==2 && downs==2 && ups==0);
    CHECK(k.st.echo_frames==1);

    char root[]="/tmp/mk20-r1-hid-XXXXXX", path[128], old[128], stop_path[128];
    CHECK(mkdtemp(root)!=NULL); snprintf(path,sizeof path,"%s/hid",root);
    snprintf(old,sizeof old,"%s/old",root); snprintf(stop_path,sizeof stop_path,"%s/stop",root);
    CHECK(!mkfifo(path,0600)); int hr=open(path,O_RDONLY|O_NONBLOCK); CHECK(hr>=0);
    hid_t h; hid_init(&h,path,stop_path); k.hid=c.hid=&h; kbd_service(&k,now); read_report(hr,0);
    key(&k,16,1); read_report(hr,HID_MOD_RALT);
    int sf=open(stop_path,O_CREAT|O_WRONLY,0600); CHECK(sf>=0); close(sf);
    now+=100; kbd_service(&k,now); read_report(hr,0); CHECK(h.fd<0);
    CHECK(!unlink(stop_path)); now+=250; kbd_service(&k,now); read_report(hr,0);
    key(&k,16,1); read_report(hr,HID_MOD_RALT);
    CHECK(!rename(path,old)); CHECK(!mkfifo(path,0600)); int hr2=open(path,O_RDONLY|O_NONBLOCK); CHECK(hr2>=0);
    now=h.next_check_ms; beat(&k); kbd_service(&k,now); CHECK(h.fd<0 && h.nparked==1);
    now+=200; kbd_service(&k,now);
    uint8_t reports[32], want[18]={1,0,0,0,0,0,0,0,0,1,0x40,0,0,0,0,0,0,0};
    CHECK(read(hr2,reports,sizeof reports)==18 && !memcmp(reports,want,18));
    CHECK(read(hr,reports,sizeof reports)<=0);

    /* First blocked send and a full bounded output queue cannot delay HID up. */
    int p[2]; CHECK(!pipe(p)); fcntl(p[1],F_SETFL,O_NONBLOCK); fcntl(p[0],F_SETFL,O_NONBLOCK);
    uint8_t fill[1024]={0}; while (write(p[1],fill,sizeof fill)>0) {}
    close(sv[0]); close(sv[1]); k.host_fd=p[1];
    for (int i=0;i<100;i++) json(&k,"{\"method\":\"getInfo\"}");
    CHECK(k.host_out_len<=sizeof k.host_out && k.st.host_tx_timeouts>0);
    key(&k,16,0); read_report(hr2,0); CHECK(!h.mods);
    key(&k,16,1); read_report(hr2,HID_MOD_RALT);
    /* The actual SIGTERM path uses these same HID teardown functions. */
    CHECK(!hid_release_all(&h,1500)); hid_close(&h,0); read_report(hr2,0);
    hid_close_parked(&h); k.hid=c.hid=NULL; k.host_fd=-1;
    close(p[0]); close(p[1]); close(hr); close(hr2);
    unlink(path); unlink(old); CHECK(!rmdir(root)); kbd_free(&k);

    for (int mode=0;mode<2;mode++) { /* --no-gd32-write / kill-file share this input path */
        init(&k,&c); k.gd32_rw=0; key(&k,1,1); pump(&k);
        CHECK(onboard_current(&k)==ONBOARD_HOME && k.scr.pending[0][0]==0xffff);
        key(&k,1,0); pump(&k); uint16_t expected[128*128];
        CHECK(!ui_render_panel(onboard_ui(&k),1,expected,128*128));
        CHECK(!memcmp(expected,k.scr.pending[0],sizeof expected)); kbd_free(&k);
    }
}
static void r1_press_owners(void)
{
    kbd_t k; ctl_t c; init(&k,&c); onboard_route(&k,ONBOARD_L1);
    hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); jpeg(&k); finish_decode(&k);
    int sv[2]; CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,sv)); k.host_fd=sv[0]; fcntl(sv[1],F_SETFL,O_NONBLOCK);
    /* fb12 (col 1): fb17 is the Mac Backspace HID key on live L1/L4 since 2026-10-21. */
    key(&k,12,1); knob(&k,103,1); now+=20; knob(&k,103,0); key(&k,12,0);
    CHECK(onboard_current(&k)==ONBOARD_L4);
    uint8_t buf[2048]; ssize_t n=read(sv[1],buf,sizeof buf); CHECK(n>0);
    host_parser p; host_parser_init(&p,sizeof buf); if(n>0) host_push(&p,buf,(size_t)n);
    host_frame f; int count=0;
    while (host_next(&p,&f)) {
        char expected[160]; int slot=count<2 ? 1 : 103, row=count<2 ? 2 : 103;
        int len=host_json_key_state(expected,sizeof expected,slot,row,(count%2)==0);
        CHECK(f.cmd==101 && f.size==(uint32_t)len && !memcmp(expected,f.data,f.size)); count++;
    }
    CHECK(count==4); host_parser_free(&p); close(sv[0]); close(sv[1]); k.host_fd=-1;

    int a[2],b[2]; CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,a)); CHECK(!socketpair(AF_UNIX,SOCK_STREAM,0,b));
    c.cfd[0]=a[0]; c.cfd[1]=b[0]; fcntl(a[1],F_SETFL,O_NONBLOCK); fcntl(b[1],F_SETFL,O_NONBLOCK);
    ctl_set_allowed(&c,1);
    c.screen.page[3]=(ctl_page_t){.used=1,.id=3,.client=0,.bound=1};
    c.screen.page[4]=(ctl_page_t){.used=1,.id=4,.client=1,.bound=1};
    c.screen.current=3; kbd_safety_tick(&k,now); key(&k,1,1);
    n=read(a[1],buf,sizeof buf-1); buf[n>0?n:0]=0; CHECK(n>0 && strstr((char *)buf,"\"down\":true"));
    c.screen.current=4; kbd_safety_tick(&k,now); key(&k,1,0);
    n=read(a[1],buf,sizeof buf-1); buf[n>0?n:0]=0; CHECK(n>0 && strstr((char *)buf,"\"page\":3") && strstr((char *)buf,"\"down\":false"));
    CHECK(read(b[1],buf,sizeof buf)<0); /* page 4 must not receive page 3's up */
    key(&k,1,1); n=read(b[1],buf,sizeof buf); CHECK(n>0);
    /* Disconnect + reused client slot must not inherit an old press. */
    ctl_client_gone(&c,1,now); c.cfd[1]=dup(a[0]); CHECK(c.cfd[1]>=0);
    key(&k,1,0); CHECK(read(a[1],buf,sizeof buf)<0);
    ctl_close(&c); close(a[1]); close(b[1]); kbd_free(&k);
}
static void r1_reserved_and_confirmation(void)
{
    kbd_t k; ctl_t c; init(&k,&c);
    uint16_t safety[128*128], footer[428*30];
    memcpy(safety,k.scr.pending[19],sizeof safety); memcpy(footer,k.scr.pending[20]+428*112,sizeof footer);
    k.scr.dirty[19]=1;
    onboard_route(&k,ONBOARD_L5);
    /* Inspect the queued intermediate frames BEFORE any replacement render. */
    CHECK(k.scr.dirty[19]==1 && !memcmp(safety,k.scr.pending[19],sizeof safety));
    CHECK(!memcmp(footer,k.scr.pending[20]+428*112,sizeof footer));
    CHECK(!k.scr.dirty[20]);
    kbd_service(&k,now); CHECK(!memcmp(safety,k.scr.pending[19],sizeof safety));
    CHECK(!memcmp(footer,k.scr.pending[20]+428*112,sizeof footer));

    char root[]="/tmp/mk20-r1-confirm-XXXXXX", path[256]; CHECK(mkdtemp(root)!=NULL);
    for(int fb=1;fb<=21;fb++) { snprintf(path,sizeof path,"%s/fb%d",root,fb); int fd=open(path,O_CREAT|O_EXCL|O_RDWR,0600); CHECK(fd>=0); close(fd); }
    snprintf(k.scr.fbdir,sizeof k.scr.fbdir,"%s",root); ctl_set_allowed(&c,1);
    req(&c,"{\"op\":\"confirm.request\",\"cid\":1,\"risk\":\"low\",\"summary\":\"r1\",\"timeout_ms\":10000}","true");
    pump(&k); CHECK(!c.confirm.shown); CHECK(kbd_flush(&k)>=0); kbd_service(&k,now); CHECK(c.confirm.shown);
    now+=500; onboard_route(&k,ONBOARD_HOME); CHECK(!c.confirm.shown && !c.confirm.shown_ms);
    onboard_route(&k,ONBOARD_L5); key(&k,11,1); key(&k,11,0); CHECK(c.confirm.pending && !c.confirm.shown);
    pump(&k); CHECK(!c.confirm.shown); CHECK(kbd_flush(&k)>=0); kbd_service(&k,now);
    CHECK(c.confirm.shown && c.confirm.shown_ms==now);
    now+=299; key(&k,11,1); key(&k,11,0); CHECK(c.confirm.pending);
    now++; key(&k,11,1); CHECK(!c.confirm.pending);
    kbd_free(&k); for(int fb=1;fb<=21;fb++) { snprintf(path,sizeof path,"%s/fb%d",root,fb); unlink(path); } CHECK(!rmdir(root));
}
static void r1_tile_budget(void)
{
    kbd_t k; ctl_t c; init(&k,&c); onboard_route(&k,ONBOARD_L1);
    hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k); jpeg(&k); finish_decode(&k);
    FILE *acks=tmpfile(); CHECK(acks!=NULL); k.host_fd=fileno(acks);
    uint16_t px=0x1234; uint16_t before=k.scr.pending[0][0];
    uint8_t data[11]={1,0,0,0,0,1,0,1,0,0x34,0x12};
    host_frame f={123,HOST_CMD_TILE565,sizeof data,data}; kbd_on_host_frame(&k,&f);
    CHECK(k.st.tiles==1);
    CHECK(k.scr.pending[0][0]==before); /* receiving/acking does not wake the writer */
    for(int i=0;i<4;i++) kbd_service(&k,now); /* shares the JPEG cooldown; drain reserved renders */
    CHECK(k.scr.pending[0][0]==before && onboard_timeout(&k,now,100)>0);
    for(int i=0;i<1000;i++) {
        px=(uint16_t)(0x2000+i); data[9]=(uint8_t)px; data[10]=(uint8_t)(px>>8);
        kbd_on_host_frame(&k,&f); CHECK(k.st.tiles==(unsigned)i+2); kbd_service(&k,now);
    }
    CHECK(k.scr.pending[0][0]==before);
    data[0]=2; kbd_on_host_frame(&k,&f); CHECK(k.st.tiles==1001); /* bounded slot, E instead of growth */
    fflush(acks); rewind(acks); host_parser parser; host_parser_init(&parser,32768);
    uint8_t bytes[32768]; size_t len=fread(bytes,1,sizeof bytes,acks); host_push(&parser,bytes,len);
    int count=0;
    while(host_next(&parser,&f)) { CHECK(f.id==123 && f.cmd==200 && f.size==(count==1001 ? 1u : 0u)); if(f.size) CHECK(f.data[0]=='E'); count++; }
    CHECK(count==1002); host_parser_free(&parser); fclose(acks); k.host_fd=-1;
    now+=249; kbd_service(&k,now); CHECK(k.scr.pending[0][0]==before);
    now++; kbd_service(&k,now); CHECK(k.scr.pending[0][0]==px);
    px=0x4321; CHECK(!onboard_tile(&k,1,0,0,1,1,&px));
    unsigned decoded=k.st.jpg_ok; jpeg(&k);
    for(int i=0;i<1000 && k.st.jpg_ok==decoded;i++) { kbd_service(&k,now); usleep(1000); }
    CHECK(k.st.jpg_ok==decoded+1 && !onboard_tile_allowed(&k,1,0,1));
    CHECK(k.scr.pending[0][0]==(uint16_t)(0x2000+999)); /* JPEG waits for tile budget */
    now+=249; kbd_service(&k,now); CHECK(k.scr.pending[0][0]==(uint16_t)(0x2000+999));
    now++; for(int i=0;i<64;i++) kbd_service(&k,now);
    CHECK(onboard_tile_allowed(&k,1,0,1) && k.scr.pending[0][0]!=px);
    /* Ready normal work cannot starve the reserved overlay passes. */
    now+=250; before=k.scr.pending[1][0]; px=0xabcd;
    CHECK(!onboard_tile(&k,2,0,0,1,1,&px)); c.ui.dirty=1;
    kbd_service(&k,now); CHECK(k.scr.pending[1][0]==before);
    kbd_service(&k,now); CHECK(k.scr.pending[1][0]==before);
    kbd_service(&k,now); CHECK(k.scr.pending[1][0]==px);
    onboard_route(&k,ONBOARD_HOME); pump(&k); CHECK(k.scr.pending[0][0]!=px);
    kbd_free(&k);
}
static void round1_regressions(void)
{
    r1_queued_hello_and_hid(); r1_press_owners(); r1_reserved_and_confirmation(); r1_tile_budget();
}
