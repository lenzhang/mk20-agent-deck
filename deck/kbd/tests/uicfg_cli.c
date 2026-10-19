#include "uicfg.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char **argv)
{
    if (argc==2 && !strcmp(argv[1],"catalogue")) {
        char label[1024];
        fputs("[",stdout);
        for (size_t i=0;i<UI_PROVIDER_COUNT;i++) {
            const ui_provider_t *p=&UI_PROVIDERS[i];
            if (uicfg_quote(p->label,label,sizeof label)) return 1;
            printf("%s{\"id\":\"%s\",\"label\":%s,\"base_url\":\"%s\",\"domestic\":%s,\"supported\":%s,\"models\":[",i?",":"",p->id,label,p->base_url,p->domestic?"true":"false",p->supported?"true":"false");
            for (size_t j=0;j<p->model_count;j++) printf("%s{\"id\":\"%s\",\"vision\":%s,\"tools\":%s}",j?",":"",p->models[j].id,p->models[j].vision?"true":"false",p->models[j].tools?"true":"false");
            fputs("]}",stdout);
        }
        fputs("]",stdout); return 0;
    }
    if (argc==3 && !strcmp(argv[1],"quote")) { char out[4096]; if (uicfg_quote(argv[2],out,sizeof out)) return 1; fputs(out,stdout); return 0; }
    if (argc!=8) return 2;
    uicfg_t c; uicfg_defaults(&c); c.provider=atoi(argv[2]); c.model=atoi(argv[3]); c.wifi=atoi(argv[4]); c.proxy_port=atoi(argv[5]);
    if (strlen(argv[6])>=sizeof c.proxy_host || strlen(argv[7])>=sizeof c.wifi_proxy) return 2;
    strcpy(c.proxy_host,argv[6]); strcpy(c.wifi_proxy,argv[7]);
    return uicfg_apply(argv[1],&c,NULL)?1:0;
}
