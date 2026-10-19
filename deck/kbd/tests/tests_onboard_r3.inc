/* Round 3: protocol acks must pass pending releases without letting ordinary
 * output overtake them. Fake clock, nonblocking pipes, real host framing only. */
static void r3_ack_backpressure(int probe, int stale)
{
    kbd_t k; ctl_t c; r2_online(&k,&c);
    int p[2]; CHECK(!pipe(p)); k.host_fd=p[1];
    CHECK(!fcntl(p[0],F_SETFL,O_NONBLOCK)); CHECK(!fcntl(p[1],F_SETFL,O_NONBLOCK));
    key(&k,17,1);
    int downs,ups; CHECK(host_events(p[0],&downs,&ups)==1 && downs==1 && !ups);
    if(probe) r2_saturate(&k,p[1]); /* exact saved re-acceptance probe */
    else {
        char fill[1024]={0}; while(write(p[1],fill,sizeof fill)>0) {}
        for(int i=0;i<200;i++) json(&k,"{\"method\":\"getInfo\"}");
        /* Fill ordinary output precisely, leaving only protocol reserves. */
        while(!kbd_host_queue(&k,500,HOST_CMD_JSON,"x",1)) {}
    }
    uint8_t tile_data[11]={1,0,0,0,0,1,0,1,0,0x34,0x12}, wire[64];
    if(stale) {
        CHECK(host_encode(123,HOST_CMD_TILE565,tile_data,sizeof tile_data,wire,sizeof wire)==35);
        kbd_host_feed(&k,wire,10);
    }
    if(probe || stale) { knob(&k,103,1); now+=20; knob(&k,103,0); }
    key(&k,17,0);
    CHECK(kbd_host_send(&k,501,HOST_CMD_JSON,"later",5)==-1);
    size_t queued=k.host_out_len;
    unsigned sent=k.st.host_tx;
    int tiles=0, jpgs=probe ? 1 : 64;
    if(!probe) {
        /* Success, rejection and stale-frame error acks all use the reserve. */
        if(stale) { kbd_host_feed(&k,wire+10,25); tiles++; }
        else {
            host_frame f={123,HOST_CMD_TILE565,sizeof tile_data,tile_data};
            kbd_on_host_frame(&k,&f); tiles++;
        }
        for(int i=tiles;i<64;i++) { tile(&k,20,0,1); tiles++; }
    }
    for(int i=0;i<jpgs;i++) jpeg(&k);
    CHECK(k.st.host_tx==sent+(unsigned)(tiles+jpgs));
    CHECK(k.host_out_len>=queued+(size_t)(tiles+jpgs)*(HOST_HDR+4));
    CHECK(k.host_out_len<=sizeof k.host_out);
    /* Non-ack output, even with an image cmd, cannot consume ack reserves. */
    CHECK(kbd_host_send(&k,502,HOST_CMD_JPG,"later",5)==-1);
    CHECK(kbd_host_send(&k,503,HOST_CMD_TILE565,"later",5)==-1);
    host_events(p[0],&downs,&ups); /* only pipe filler has reached the reader */
    host_parser parser; CHECK(!host_parser_init(&parser,65536));
    host_frame f; int acks=0, first_tile=0, other_tiles=0, events=0;
    for(int i=0;i<200;i++) {
        kbd_host_drain(&k);
        uint8_t bytes[4096]; ssize_t n;
        while((n=read(p[0],bytes,sizeof bytes))>0) host_push(&parser,bytes,(size_t)n);
        while(host_next(&parser,&f)) {
            if(f.cmd==HOST_CMD_JPG) { CHECK(f.id==99 && !f.size); acks++; }
            if(f.cmd==HOST_CMD_TILE565 && !probe) {
                if(f.id==123) {
                    CHECK(stale ? f.size==1 && f.data[0]=='E' : !f.size); first_tile++;
                } else { CHECK(f.id==77 && f.size==1 && f.data[0]=='E'); other_tiles++; }
            }
            if(f.cmd==HOST_CMD_JSON && f.id==KBD_EVENT_ID) {
                char j[160]; int len=events ? host_json_key_state(j,sizeof j,103,103,events==1) :
                    host_json_key_state(j,sizeof j,1,3,0);
                CHECK(f.size==(uint32_t)len && !memcmp(f.data,j,f.size)); events++;
            }
            CHECK(f.id!=501 && f.id!=502 && f.id!=503);
        }
    }
    CHECK(acks==jpgs); CHECK(first_tile==(!probe)); CHECK(other_tiles==(probe ? 0 : tiles-1));
    CHECK(events==(probe || stale ? 3 : 1)); CHECK(!k.host_out_len && !k.host_out_off);
    CHECK(!parser.bad_size_crc && !parser.bad_data_crc && !parser.dropped_bytes);
    /* Release ownership clears once, including after another physical up. */
    key(&k,17,0); kbd_host_drain(&k); CHECK(!host_events(p[0],&downs,&ups));
    CHECK(!kbd_host_send(&k,504,HOST_CMD_JSON,"later",5));
    uint8_t bytes[128]; ssize_t n=read(p[0],bytes,sizeof bytes); CHECK(n>0);
    if(n>0) host_push(&parser,bytes,(size_t)n);
    CHECK(host_next(&parser,&f) && f.id==504 && f.cmd==HOST_CMD_JSON && f.size==5);
    host_parser_free(&parser); close(p[0]); close(p[1]); k.host_fd=-1; kbd_free(&k);
}
static void round3_regressions(void)
{
    r3_ack_backpressure(1,0); r3_ack_backpressure(0,0); r3_ack_backpressure(0,1);
}
