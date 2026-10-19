/* Host-only IPC driver for unchanged kbd ctl/HID code. No socket enable API:
 * keyboard-side callbacks simulate allow, render, physical confirm and estop. */
#define _POSIX_C_SOURCE 200809L
#include "ctl.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
static int64_t now_ms(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;}
int main(int argc,char **argv)
{
    if(argc!=4&&argc!=5)return 2;
    int controls=argc==5?atoi(argv[4]):-1;
    if(controls>=0)fcntl(controls,F_SETFL,fcntl(controls,F_GETFL)|O_NONBLOCK);
    int fd=atoi(argv[1]),hfd=open(argv[2],O_WRONLY|O_NONBLOCK);if(hfd<0)return 2;
    hid_t h;hid_init(&h,argv[2],"");if(hid_attach_fd(&h,hfd))return 2;
    ctl_t c;ctl_init(&c,&h);c.cfd[0]=fd;fcntl(fd,F_SETFL,fcntl(fd,F_GETFL)|O_NONBLOCK);ctl_set_allowed(&c,strcmp(argv[3],"disabled")!=0&&strcmp(argv[3],"manual")!=0);
    int early=0,stop=0;int64_t start=now_ms();
    while(c.cfd[0]>=0&&now_ms()-start<30000){
        int64_t now=now_ms();ctl_tick(&c,now);hid_service(&h,now);
        if(controls>=0){char key;while(read(controls,&key,1)==1){if(key=='a')ctl_set_allowed(&c,1);else if(key=='d')ctl_set_allowed(&c,0);else if(key=='e')ctl_estop(&c,"test_key",now);else if(key=='r')ctl_confirm_key(&c,0,now);else if(key=='c')ctl_confirm_key(&c,1,now);}}
        if(c.confirm.pending){
            ctl_confirm_shown(&c,now);
            if(!strcmp(argv[3],"early")&&!early){ctl_confirm_key(&c,1,now);early=1;}
            if(!strcmp(argv[3],"reject"))ctl_confirm_key(&c,0,now);
            else if(strcmp(argv[3],"manual")&&strcmp(argv[3],"timeout")&&now-c.confirm.shown_ms>=350)ctl_confirm_key(&c,1,now);
        }
        if(!stop&&!strncmp(argv[3],"estop-",6)&&!strcmp(c.ui.state,argv[3]+6)){ctl_estop(&c,"test_key",now);stop=1;}
        struct pollfd p[4];int n=ctl_pollfds(&c,p,4);int rc=poll(p,(nfds_t)n,5);if(rc>0)ctl_on_poll(&c,p,n);else if(rc<0&&errno!=EINTR)break;
    }
    printf("{\"ignored\":%u,\"acts\":%u,\"renews\":%u,\"estops\":%u,\"allowed\":%d}\n",c.st.confirm_ignored,c.st.acts,c.st.renews,c.st.estops,c.allowed);
    ctl_close(&c);hid_close(&h,0);return 0;
}
