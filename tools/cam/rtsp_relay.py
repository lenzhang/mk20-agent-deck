import socket,threading,sys
L=int(sys.argv[1]);H,P=sys.argv[2],int(sys.argv[3])
def pipe(a,b):
  try:
    while (d:=a.recv(65536)): b.sendall(d)
  except Exception: pass
  finally:
    for s in (a,b):
      try:s.shutdown(socket.SHUT_RDWR)
      except Exception:pass
srv=socket.socket();srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);srv.bind(('127.0.0.1',L));srv.listen(4);srv.settimeout(60)
while True:
  try:c,_=srv.accept()
  except socket.timeout: break
  u=socket.create_connection((H,P),5)
  threading.Thread(target=pipe,args=(c,u),daemon=True).start();threading.Thread(target=pipe,args=(u,c),daemon=True).start()
