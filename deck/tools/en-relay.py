# 127.0.0.1:17891 -> 169.254.7.2:17890 forced out a given interface (IP_BOUND_IF), for ECM link-local tests.
import socket, sys, threading
IF = sys.argv[1] if len(sys.argv) > 1 else "en13"
def pipe(a, b):
    try:
        while (d := a.recv(4096)): b.sendall(d)
    except OSError: pass
    for s in (a, b):
        try: s.shutdown(socket.SHUT_RDWR)
        except OSError: pass
ls = socket.socket(); ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); ls.bind(("127.0.0.1", 17891)); ls.listen(4)
while True:
    c, _ = ls.accept()
    u = socket.socket(); u.setsockopt(socket.IPPROTO_IP, 25, socket.if_nametoindex(IF)); u.connect(("169.254.7.2", 17890))
    threading.Thread(target=pipe, args=(c, u), daemon=True).start(); threading.Thread(target=pipe, args=(u, c), daemon=True).start()
