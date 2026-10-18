/* MK20 VNC auth check: RFB 3.8 handshake + VNC Authentication (type 2) against host:port using a password FILE.
 * Never prints the password. Output: "AUTH OK <w>x<h> name=<...>" or "AUTH FAILED: <reason>". Exit 0 on success.
 * usage: vnc_authcheck <host> <port> <passfile> */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include "des.h"
static int rd(int s, void *b, size_t n){size_t g=0;while(g<n){ssize_t r=read(s,(char*)b+g,n-g);if(r<=0)return -1;g+=r;}return 0;}
static int wr(int s, const void *b, size_t n){return write(s,b,n)==(ssize_t)n?0:-1;}
static int fail(const char *m){printf("AUTH FAILED: %s\n",m);return 1;}
int main(int argc,char**argv){
  if(argc!=4){fprintf(stderr,"usage: %s host port passfile\n",argv[0]);return 2;}
  char pw[9]={0}; FILE*f=fopen(argv[3],"r"); if(!f)return fail("cannot open password file");
  size_t n=fread(pw,1,8,f); fclose(f); while(n&&(pw[n-1]=='\n'||pw[n-1]=='\r'))pw[--n]=0;
  int s=socket(AF_INET,SOCK_STREAM,0); struct timeval tv={8,0};
  setsockopt(s,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof tv); setsockopt(s,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof tv);
  struct sockaddr_in a={0}; a.sin_family=AF_INET; a.sin_port=htons(atoi(argv[2]));
  if(inet_pton(AF_INET,argv[1],&a.sin_addr)!=1)return fail("bad host");
  if(connect(s,(struct sockaddr*)&a,sizeof a))return fail("connect");
  char ver[13]={0}; if(rd(s,ver,12))return fail("no version"); if(strncmp(ver,"RFB ",4))return fail("not RFB");
  if(wr(s,"RFB 003.008\n",12))return fail("send version");
  unsigned char nt; if(rd(s,&nt,1))return fail("no sectypes");
  if(nt==0)return fail("server refused connection");
  unsigned char t[256]; if(rd(s,t,nt))return fail("sectypes"); int has2=0; for(int i=0;i<nt;i++)if(t[i]==2)has2=1;
  if(!has2)return fail("server does not offer VNC password auth (type 2)");
  unsigned char c=2; if(wr(s,&c,1))return fail("choose type");
  unsigned char ch[16],rsp[16],key[8]={0}; if(rd(s,ch,16))return fail("no challenge");
  for(int i=0;i<8;i++){unsigned char b=(unsigned char)pw[i],r=0;for(int j=0;j<8;j++)if(b&(1<<j))r|=1<<(7-j);key[i]=r;}
  des_encrypt_block(key,ch,rsp); des_encrypt_block(key,ch+8,rsp+8); memset(pw,0,sizeof pw); memset(key,0,sizeof key);
  if(wr(s,rsp,16))return fail("send response");
  unsigned char res[4]; if(rd(s,res,4))return fail("no result");
  if(res[0]|res[1]|res[2]|res[3])return fail("wrong password (SecurityResult != 0)");
  unsigned char shared=1; wr(s,&shared,1); unsigned char si[24];
  if(rd(s,si,24)){printf("AUTH OK (no ServerInit)\n");return 0;}
  unsigned w=(si[0]<<8)|si[1],h=(si[2]<<8)|si[3]; unsigned nl=(si[20]<<24)|(si[21]<<16)|(si[22]<<8)|si[23];
  char name[128]={0}; if(nl>127)nl=127; rd(s,name,nl);
  printf("AUTH OK %ux%u bpp=%u name=%s\n",w,h,si[4],name); close(s); return 0;
}
