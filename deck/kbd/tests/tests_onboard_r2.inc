/* Re-acceptance probes, plus ordering, deadline and invalidation coverage.
 * Only fake clocks, nonblocking pipes and temporary files; no devices. */
static void r2_online(kbd_t *k, ctl_t *c)
{
    init(k,c); onboard_route(k,ONBOARD_L1);
    hello(k); beat(k); now+=1000; beat(k); now+=1000; beat(k);
    jpeg(k); finish_decode(k); CHECK(onboard_tile_allowed(k,1,0,1));
}
static void r2_saturate(kbd_t *k, int fd)
{
    char fill[1024]={0}; while(write(fd,fill,sizeof fill)>0) {}
    for(int i=0;i<200;i++) json(k,"{\"method\":\"getInfo\"}");
    for(int i=0;i<1000;i++) tile(k,20,0,1);
    CHECK(k->host_out_len<=sizeof k->host_out && k->st.host_tx_errors>0);
}
static void r2_host_backpressure(void)
{
    for(int mode=0;mode<3;mode++) {
        kbd_t k; ctl_t c; r2_online(&k,&c);
        int p[2]; CHECK(!pipe(p)); k.host_fd=p[1];
        CHECK(fcntl(p[0],F_SETFL,O_NONBLOCK)==0); CHECK(fcntl(p[1],F_SETFL,O_NONBLOCK)==0);
        /* Cover the reviewer's fb17 case and all simultaneously owned keys. */
        uint32_t owned=0; int count=0, downs,ups;
        for(int fb=1;fb<20;fb++) if(fb!=16 && (!mode || fb==17)) {
            key(&k,fb,1); owned|=1u<<(fb-1); count++;
        }
        CHECK(host_events(p[0],&downs,&ups)==count && downs==count && !ups);
        r2_saturate(&k,p[1]);
        if(mode!=1) { knob(&k,103,1); now+=20; knob(&k,103,0); }
        for(int fb=1;fb<20;fb++) if(owned & (1u<<(fb-1))) key(&k,fb,0);
        key(&k,17,1); key(&k,17,0); /* cannot overwrite a pending release */
        /* Repeated boundaries/releases must neither lose nor duplicate ups. */
        if(mode==0) { onboard_route(&k,ONBOARD_HOME); key(&k,17,0); }
        if(mode==2) {
            ctl_set_allowed(&c,1);
            req(&c,"{\"op\":\"lease.acquire\",\"ttl_ms\":200}","token");
            knob(&k,103,1); now+=200; kbd_service(&k,now);
            CHECK(!c.token && c.st.expiries==1);
            now+=800; kbd_service(&k,now);
            CHECK(c.estopped && onboard_current(&k)==ONBOARD_L4); /* e-stop stays on the host page */
            knob(&k,103,0);
        }
        host_events(p[0],&downs,&ups); /* discard only pipe filler */
        host_parser parser; host_parser_init(&parser,65536);
        uint8_t bytes[4096]; host_frame f; uint32_t released=0; int taps=0;
        for(int i=0;i<200;i++) {
            kbd_host_drain(&k);
            ssize_t n; while((n=read(p[0],bytes,sizeof bytes))>0) host_push(&parser,bytes,(size_t)n);
            while(host_next(&parser,&f)) if(f.cmd==HOST_CMD_JSON && f.id==KBD_EVENT_ID) {
                int matched=0;
                for(int fb=1;fb<20;fb++) if(owned & (1u<<(fb-1))) {
                    char j[160]; int len=host_json_key_state(j,sizeof j,(fb-1)%5,(fb-1)/5,0);
                    if(f.size==(uint32_t)len && !memcmp(f.data,j,f.size)) {
                        CHECK(!(released & (1u<<(fb-1)))); released|=1u<<(fb-1); matched=1;
                    }
                }
                if(!matched) {
                    char j[160]; int len=host_json_key_state(j,sizeof j,103,103,taps==0);
                    CHECK(released==owned); /* releases precede navigation under saturation */
                    CHECK(f.size==(uint32_t)len && !memcmp(f.data,j,f.size)); taps++;
                }
            }
        }
        CHECK(released==owned); CHECK(taps==(mode==1 ? 0 : 2)); CHECK(!k.host_out_len);
        /* A later physical release cannot produce a duplicate after recovery. */
        key(&k,17,0); kbd_host_drain(&k); CHECK(!host_events(p[0],&downs,&ups));
        host_parser_free(&parser); close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
    }
}
static void r2_host_disconnect(void)
{
    for(int held=0;held<2;held++) {
        kbd_t k; ctl_t c; r2_online(&k,&c);
        int p[2]; CHECK(!pipe(p)); k.host_fd=p[1];
        CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK));
        if(held) key(&k,17,1);
        int downs,ups; host_events(p[0],&downs,&ups); r2_saturate(&k,p[1]);
        knob(&k,103,1); now+=20; knob(&k,103,0);
        /* Match the transport disconnect path, with immediate fd reuse before
         * another service tick. Neither pending releases nor taps may leak. */
        close(p[0]); close(p[1]); k.host_fd=-1; k.host_out_len=k.host_out_off=0; onboard_loss(&k);
        CHECK(!pipe(p)); k.host_fd=p[1]; CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK));
        kbd_host_drain(&k); key(&k,17,0); CHECK(!host_events(p[0],&downs,&ups));
        close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
    }
}
static void r2_staged_tile_invalidation(void)
{
    for(int mode=0;mode<7;mode++) {
        kbd_t k; ctl_t c; r2_online(&k,&c);
        FILE *acks=tmpfile(); CHECK(acks!=NULL); k.host_fd=fileno(acks);
        uint16_t before=k.scr.pending[0][0];
        uint8_t data[11]={1,0,0,0,0,1,0,1,0,0x34,0x12};
        host_frame f={123,HOST_CMD_TILE565,sizeof data,data};
        kbd_on_host_frame(&k,&f); CHECK(k.st.tiles==1); CHECK(k.scr.pending[0][0]==before);
        switch(mode) {
        case 0: now+=3000; break; /* exact SUSPECT probe: tick in service */
        case 1: onboard_route(&k,ONBOARD_HOME); onboard_route(&k,ONBOARD_L1); break;
        case 2: json(&k,"{\"method\":\"ui.page\",\"page\":\"L1\",\"generation\":1}"); break;
        case 3: hello(&k); break;
        case 4: onboard_loss(&k); break;
        case 5: ctl_set_allowed(&c,1); key(&k,20,1); break; /* e-stop with the agent active */
        case 6: onboard_route(&k,ONBOARD_L4); break;
        }
        for(int i=0;i<64;i++) kbd_service(&k,now);
        if(mode==5) {
            /* E-stop no longer leaves L1/L4 or invalidates the host's own page content. */
            CHECK(c.estopped && onboard_current(&k)==ONBOARD_L1); now+=250;
            for(int i=0;i<64;i++) kbd_service(&k,now);
            CHECK(k.scr.pending[0][0]==0x1234 && onboard_tile_allowed(&k,1,0,1));
        } else {
            CHECK(k.scr.pending[0][0]!=0x1234);
            if(!mode) { CHECK(onboard_health(&k)==HOST_SUSPECT); CHECK(k.scr.pending[0][0]==before); }
            CHECK(!onboard_tile_allowed(&k,1,0,1));
        }
        /* A handled tile keeps exactly its original success ack even if discarded. */
        fflush(acks); rewind(acks); uint8_t bytes[2048]; size_t n=fread(bytes,1,sizeof bytes,acks);
        host_parser parser; host_parser_init(&parser,sizeof bytes); host_push(&parser,bytes,n); int count=0;
        while(host_next(&parser,&f)) if(f.cmd==HOST_CMD_TILE565) { CHECK(f.id==123 && !f.size); count++; }
        CHECK(count==1); host_parser_free(&parser);
        /* New eligible epoch must not resurrect the stale slot. */
        onboard_route(&k,ONBOARD_L1); hello(&k); beat(&k); now+=1000; beat(&k); now+=1000; beat(&k);
        jpeg(&k); finish_decode(&k); now+=250;
        for(int i=0;i<64;i++) kbd_service(&k,now);
        CHECK(onboard_tile_allowed(&k,1,0,1)); CHECK(k.scr.pending[0][0]!=0x1234);
        uint16_t px=0x4321; CHECK(!onboard_tile(&k,1,0,0,1,1,&px));
        now+=250; for(int i=0;i<8;i++) kbd_service(&k,now); CHECK(k.scr.pending[0][0]==px);
        fclose(acks); k.host_fd=-1; kbd_free(&k);
    }
}
static void round2_regressions(void)
{
    r2_host_backpressure(); r2_host_disconnect(); r2_staged_tile_invalidation();
}
