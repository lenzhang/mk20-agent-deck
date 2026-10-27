import os,re,sys,tempfile,time
line=sys.argv[-1]; t=sys.argv[1] if len(sys.argv)>2 else time.strftime('%H:%M')
p='PROGRESS.md'; s=open(p).read()
s=re.sub(r'最后更新：\d{4}-\d{2}-\d{2} \d{2}:\d{2} SGT','最后更新：%s %s SGT'%(time.strftime('%Y-%m-%d'),t),s,count=1)
a='## 2. 关键进展日志（新的在上）\n'; i=s.index(a)+len(a)
while s[i]=='\n': i+=1
s=s[:i]+'- **%s %s** %s\n'%(time.strftime('%m-%d'),t,line)+s[i:]
fd,tmp=tempfile.mkstemp(dir='.',prefix='.PROGRESS.'); os.write(fd,s.encode()); os.close(fd); os.chmod(tmp,0o644); os.replace(tmp,p)
