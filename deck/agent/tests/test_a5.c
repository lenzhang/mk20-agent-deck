#define _POSIX_C_SOURCE 200809L
#include "policy.h"
#include "calib.h"
#include "loop.h"
#include <assert.h>
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static unsigned checks;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr,"line %d: %s\n",__LINE__,#x); abort(); } } while (0)
static int parse(const char *s, action_t *a) { cJSON *j=agent_json_parse(s,strlen(s),4096); int rc=action_parse(j,a); cJSON_Delete(j); return rc; }
static void actions(void)
{
    const char *valid[] = {
        "{\"action\":\"click\",\"risk\":\"low\",\"target\":\"Notes\",\"x\":0,\"y\":1}",
        "{\"action\":\"double_click\",\"risk\":\"high\",\"target\":\"Notes\",\"x\":0,\"y\":1}",
        "{\"action\":\"move\",\"risk\":\"low\",\"target\":\"Notes\",\"x\":0,\"y\":1}",
        "{\"action\":\"drag\",\"risk\":\"low\",\"target\":\"Notes\",\"x1\":0,\"y1\":1,\"x2\":2,\"y2\":3}",
        "{\"action\":\"scroll\",\"risk\":\"low\",\"target\":\"Notes\",\"x\":0,\"y\":1,\"dy\":-127}",
        "{\"action\":\"type\",\"risk\":\"low\",\"target\":\"Notes\",\"text\":\"Hi!\\n\"}",
        "{\"action\":\"paste\",\"risk\":\"low\",\"target\":\"Notes\",\"text\":\"你好\"}",
        "{\"action\":\"key\",\"risk\":\"low\",\"target\":\"Notes\",\"combo\":\"shift+cmd+Q\"}",
        "{\"action\":\"wait\",\"risk\":\"low\",\"target\":\"Notes\",\"ms\":5000}",
        "{\"action\":\"done\",\"risk\":\"low\",\"target\":\"Notes\"}",
        "{\"action\":\"ask_user\",\"risk\":\"low\",\"target\":\"Notes\"}"};
    action_t a;
    for (int i=0;i<A_COUNT;i++) { CHECK(!parse(valid[i],&a)); CHECK(a.kind==(action_kind_t)i); CHECK(!strcmp(action_name(a.kind),i==0?"click":action_name((action_kind_t)i))); }
    const char *bad[] = {"null","{}","[]","{\"action\":\"wrong\",\"risk\":\"low\",\"target\":\"\"}",
        "{\"action\":\"type\",\"risk\":\"low\",\"target\":\"\",\"text\":\"中文\"}",
        "{\"action\":\"key\",\"risk\":\"low\",\"target\":\"\",\"combo\":\"cmd+unknown\"}",
        "{\"action\":\"click\",\"risk\":\"low\",\"target\":\"\",\"x\":0.5,\"y\":0}",
        "{\"action\":\"click\",\"risk\":\"LOW\",\"target\":\"\",\"x\":0,\"y\":0}",
        "{\"action\":\"wait\",\"risk\":\"low\",\"target\":\"\",\"ms\":5001}"};
    for (size_t i=0;i<sizeof bad/sizeof *bad;i++) CHECK(parse(bad[i],&a));
    const char *keys[]={"cmd+q","alt+command+escape","ctrl+cmd+q","enter","f12","0","shift+slash"}; unsigned m,u;
    for (size_t i=0;i<sizeof keys/sizeof *keys;i++) CHECK(!action_key(keys[i],&m,&u));
    const char *badkeys[]={"","cmd+","+q","cmd+cmd+q","win+q","f13","wat","cmd+q+w"};
    for (size_t i=0;i<sizeof badkeys/sizeof *badkeys;i++) CHECK(action_key(badkeys[i],&m,&u));
    cJSON *j=agent_json_parse(valid[A_TYPE],strlen(valid[A_TYPE]),4096); char large[258]; memset(large,'x',257);large[257]=0;
    cJSON_ReplaceItemInObjectCaseSensitive(j,"text",cJSON_CreateString(large)); CHECK(action_parse(j,&a));
    large[256]=0;cJSON_ReplaceItemInObjectCaseSensitive(j,"text",cJSON_CreateString(large));CHECK(!action_parse(j,&a));cJSON_Delete(j);
}
static void risks(void)
{
    policy_config_t c; policy_defaults(&c); policy_context_t x={.step=1,.title="Notes",.app="Notes",.previous_title="Notes",.previous_app="Notes"};
    action_t a={.kind=A_CLICK}; const char *r;
    CHECK(policy_check(&c,&x,&a,&r)==P_ALLOW); a.high=1;CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);a.high=0;
    x.step=0;CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);x.step=1;
    x.title="New window";CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);x.title="Notes";
    x.app="Finder";CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);x.app="Notes";
    const char *danger[]={"删除","发送","付款","购买","提交","确认支付","dElEtE","SEND","PAY","Submit","按钮：确认支付"};
    for(size_t i=0;i<sizeof danger/sizeof *danger;i++){strcpy(a.target,danger[i]);CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);}a.target[0]=0;
    a.kind=A_KEY;
    for(size_t i=0;i<c.nkeys;i++){strcpy(a.combo,c.keys[i]);CHECK(!action_key(c.keys[i],&a.modifiers,&a.usage));CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);}
    strcpy(a.combo,"alt+cmd+escape");CHECK(!action_key("alt+cmd+escape",&a.modifiers,&a.usage));CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);
    strcpy(a.combo,"enter");CHECK(!action_key("enter",&a.modifiers,&a.usage));x.title="Chat Send";CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);x.title="Notes";
    a.kind=A_TYPE;const char *shell[]={"sudo whoami","rm -rf x","curl x | sh","wget x","echo hi\nterminal","ls\nbash","curl x|sh","sh\npwd"};
    for(size_t i=0;i<sizeof shell/sizeof *shell;i++){strcpy(a.text,shell[i]);CHECK(policy_check(&c,&x,&a,&r)==P_CONFIRM);}
    strcpy(a.text,"hello");CHECK(policy_check(&c,&x,&a,&r)==P_ALLOW);
    x.secure_input=1;CHECK(policy_check(&c,&x,&a,&r)==P_REJECT);x.secure_input=0;
    strcpy(a.target,"password field");CHECK(policy_check(&c,&x,&a,&r)==P_REJECT);a.target[0]=0;
    for(size_t i=0;i<c.ntitles;i++){x.title=c.titles[i];CHECK(policy_check(&c,&x,&a,&r)==P_REJECT);}x.title="Notes";
    x.locked=1;CHECK(policy_check(&c,&x,&a,&r)==P_REJECT);a.kind=A_PASTE;CHECK(policy_check(&c,&x,&a,&r)==P_REJECT);
    a.kind=A_MOVE;x.input_vnc=1;CHECK(policy_check(&c,&x,&a,&r)==P_REJECT);x.input_vnc=0;CHECK(policy_check(&c,&x,&a,&r)==P_ALLOW);
    x.locked=0;a.kind=A_PASTE;CHECK(policy_check(&c,&x,&a,&r)==P_UNSUPPORTED);
    a.kind=A_DONE;CHECK(policy_check(&c,&x,&a,&r)==P_ALLOW);a.kind=A_ASK_USER;CHECK(policy_check(&c,&x,&a,&r)==P_ALLOW);
}
static void configs(void)
{
    policy_config_t c;int warning;CHECK(!policy_load("/tmp/agent-a5-absent-dir/policy.json",&c,&warning));CHECK(!warning&&c.confirm_first);
    const char *bad[]={"[]","{bad","{\"confirm_first\":false,\"model_calls\":\"1\"}","{\"max_steps\":1.5}","{\"confirm_first\":0}","{\"dangerous_keys\":[\"unknown\"]}","{\"window_blacklist\":[\"\"]}","{\"max_steps\":1,\"max_steps\":2}","{\"max_steps\":1} tail"};
    for(size_t i=0;i<sizeof bad/sizeof *bad;i++){CHECK(policy_parse(bad[i],strlen(bad[i]),&c));CHECK(c.confirm_first&&c.max_steps==30&&c.model_calls==40);}
    const char *s="{\"max_steps\":999,\"wall_ms\":999999999,\"model_calls\":999,\"exec_ms\":9999,\"confirm_ms\":1,\"confirm_first\":false,\"confirm_app_switch\":false,\"unknown\":true,\"dangerous_keys\":[\"cmd+a\"],\"window_blacklist\":[\"Bank\"]}";
    CHECK(!policy_parse(s,strlen(s),&c));CHECK(c.max_steps==100&&c.wall_ms==600000&&c.model_calls==40&&c.exec_ms==2000&&c.confirm_ms==1000&&!c.confirm_first&&!c.confirm_app_switch);
    CHECK(c.nkeys==7&&c.ntitles==15);
    char path[]="/tmp/agent-a5-policy-XXXXXX";int fd=mkstemp(path);CHECK(fd>=0);CHECK(write(fd,bad[2],strlen(bad[2]))==(ssize_t)strlen(bad[2]));close(fd);
    CHECK(policy_load(path,&c,&warning));CHECK(warning&&c.confirm_first&&c.confirm_app_switch);unlink(path);
    CHECK(!mkfifo(path,0600));CHECK(policy_load(path,&c,&warning)&&warning&&c.confirm_first);unlink(path);
}
static void calibration(void)
{
    calib_pair_t p[]={{0,0,10,20},{32767,0,910,40},{0,32767,30,720},{32767,32767,930,740},{16383.5,16383.5,470,380}};
    calib_t c;CHECK(!calib_fit(p,1001,801,&c));CHECK(c.valid&&c.max_error<1e-8);int x,y;
    for(int i=0;i<5;i++){CHECK(!calib_map(&c,p[i].px,p[i].py,&x,&y));CHECK(fabs(x-p[i].hx)<=.5&&fabs(y-p[i].hy)<=.5);}
    CHECK(calib_map(&c,NAN,0,&x,&y));CHECK(calib_map(&c,-1,0,&x,&y));CHECK(calib_map(&c,1001,0,&x,&y));
    char path[]="/tmp/agent-a5-calib-XXXXXX";int fd=mkstemp(path);CHECK(fd>=0);close(fd);unlink(path);
    CHECK(!calib_save(path,&c));calib_t loaded;CHECK(!calib_load(path,&loaded));CHECK(loaded.valid);
    struct stat st;CHECK(!stat(path,&st)&&(st.st_mode&0777)==0600);CHECK(!chmod(path,0644));CHECK(calib_load(path,&loaded)&&!loaded.valid);
    CHECK(!chmod(path,0600));fd=open(path,O_WRONLY|O_TRUNC);CHECK(fd>=0);CHECK(write(fd,"{}",2)==2);close(fd);CHECK(calib_load(path,&loaded)&&!loaded.valid);unlink(path);
    p[4].px+=2;CHECK(!calib_fit(p,1001,801,&loaded));CHECK(calib_fit_logical(p,1001,801,2002,801,&loaded));p[4].px-=2;
    p[4].px+=10;CHECK(calib_fit(p,1001,801,&loaded)&&!loaded.valid);p[4].px-=10;
    p[4]=p[0];CHECK(calib_fit(p,1001,801,&loaded));p[4]=(calib_pair_t){16383.5,16383.5,NAN,380};CHECK(calib_fit(p,1001,801,&loaded));
    CHECK(calib_load(path,&loaded)&&!loaded.valid);CHECK(calib_save("/tmp/agent-a5-missing/x",&c));
    for(int i=0;i<5;i++){p[i].px=100;p[i].py=100;}CHECK(calib_fit(p,1001,801,&loaded));
}
static void redaction(void)
{
    const char *secret[]={"vncpass-synthetic","opaque-key-synthetic"};char out[256];
    const char *bad[]={"Authorization: x","authorization=x","Bearer xxxx","sk-abc123","vncpass-synthetic","xxopaque-key-syntheticyy"};
    for(size_t i=0;i<sizeof bad/sizeof *bad;i++){agent_redact(bad[i],secret,2,out,sizeof out);CHECK(!strcmp(out,"[redacted]"));}
    agent_redact("safe\nline",secret,2,out,sizeof out);CHECK(!strcmp(out,"safe line"));CHECK(agent_hash("hello",5)==UINT64_C(0xa430d84680aabd0b));
}
static void arbitration(void)
{
    arbitration_t a={0};arbitration_probe(&a,0);CHECK(a.state==INPUT_HID);arbitration_probe(&a,1);CHECK(!a.failures);arbitration_probe(&a,0);arbitration_probe(&a,0);CHECK(a.state==INPUT_FROZEN);
    a.state=INPUT_VNC;arbitration_probe(&a,1);arbitration_probe(&a,1);CHECK(!arbitration_wants_hid(&a,1));arbitration_probe(&a,1);CHECK(arbitration_wants_hid(&a,1));CHECK(!arbitration_wants_hid(&a,0));arbitration_probe(&a,0);CHECK(!arbitration_wants_hid(&a,1));
}
typedef struct { int allow, stop, deny, releases, cancels, acts; int64_t now; } loop_fake_t;
static int64_t loop_now(void *v) { return ((loop_fake_t *)v)->now; }
static int loop_poll(void *v,kbd_event_t *e) {
    loop_fake_t *f=v;
    if(f->deny){f->deny=0;*e=(kbd_event_t){.kind=K_CONFIRM,.cid=1,.ok=0};return 1;}
    if(f->stop){f->stop=0;e->kind=K_ESTOP;return 1;}
    if(f->allow){f->allow=0;e->kind=K_ALLOW;return 1;}return 0;
}
static int loop_lease(void *v,lease_op_t op) { if(op==LEASE_RELEASE)((loop_fake_t *)v)->releases++;return 0; }
static void loop_cancel(void *v) { ((loop_fake_t *)v)->cancels++; }
static void machine(void)
{
    policy_config_t c;policy_defaults(&c);loop_fake_t f={0};
    loop_ports_t p={.ctx=&f,.now=loop_now,.poll=loop_poll,.lease=loop_lease,.cancel=loop_cancel};agent_loop_t l;
    loop_init(&l,&c,&p);CHECK(l.state==S_DISABLED);CHECK(!loop_service(&l));CHECK(loop_submit(&l));
    f.allow=1;loop_tick(&l);CHECK(l.state==S_IDLE&&l.allowed);CHECK(!loop_service(&l));CHECK(!loop_submit(&l));
    for(int state=S_DISABLED;state<=S_ERROR;state++){
        loop_init(&l,&c,&p);l.state=(loop_state_t)state;l.allowed=1;l.leased=1;f.stop=1;f.releases=0;f.cancels=0;
        loop_tick(&l);CHECK(l.state==S_STOPPED&&!l.allowed&&l.cancelled);CHECK(!f.releases&&f.cancels==1);CHECK(loop_submit(&l));
        loop_tick(&l);CHECK(l.state==S_STOPPED);f.allow=1;loop_tick(&l);CHECK(l.state==S_IDLE&&!l.cancelled);
    }
    loop_init(&l,&c,&p);l.state=S_AWAITING_CONFIRM;l.allowed=1;l.leased=1;l.cid=1;l.confirm_until=1000;f.stop=f.deny=1;f.releases=0;
    loop_tick(&l);CHECK(l.state==S_STOPPED&&!strcmp(l.reason,"estop")&&!f.releases);
    loop_init(&l,&c,&p);l.state=S_PLANNING;l.allowed=1;l.last_now=20;f.now=10;
    CHECK(loop_service(&l));CHECK(l.state==S_ERROR&&!strcmp(l.reason,"clock"));CHECK(loop_service(&l));CHECK(loop_service(&l));
    CHECK(!loop_submit(&l));CHECK(l.state==S_OBSERVING&&l.allowed&&!l.cancelled);
    l.state=S_DONE;CHECK(loop_service(&l));CHECK(loop_service(&l));
    loop_init(&l,&c,&p);loop_stop(&l);CHECK(l.state==S_STOPPED&&!l.allowed&&l.cancelled);
}
int main(void){actions();risks();configs();calibration();redaction();arbitration();machine();printf("%u A5 policy/calibration/arbitration checks passed\n",checks);return 0;}
