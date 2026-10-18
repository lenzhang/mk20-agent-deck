import subprocess,socket,time,sys,os
z,out=sys.argv[1],sys.argv[2]; sock='/tmp/qm-%d.sock'%os.getpid()
p=subprocess.Popen(['qemu-system-arm','-M','virt','-cpu','cortex-a7','-m','512','-nographic','-serial','null','-kernel',z,
  '-monitor','unix:%s,server,nowait'%sock],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(float(sys.argv[3]) if len(sys.argv)>3 else 6)
s=socket.socket(socket.AF_UNIX); s.connect(sock); time.sleep(0.3); s.recv(4096)
for c in ['stop','pmemsave 0x40008000 14031052 "%s"'%out,'info registers']:
    s.send((c+'\n').encode()); time.sleep(1.5)
r=s.recv(65536).decode(errors='replace'); print([l for l in r.splitlines() if 'R15' in l or 'PC' in l][:2])
s.send(b'quit\n'); time.sleep(0.5); p.kill()
