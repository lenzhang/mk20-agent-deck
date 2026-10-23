# Host link stall on 1.6.0-rc4, 2026-10-23 20:01 (device uptime ~52 600 s)

Symptom: mk20deck "连接中断：连续 3 帧无 ACK" at 20:01:22, then every TCP handshake timed out; daemon idle "未找到 MK20 CDC 端口".
Soak samples stayed ok (it does not check host-link liveness): bridge_clients 2, ping 0 % loss.

Device state (20:45-20:50):
- ttygs_tcp_bridge (pid 2752) blocked in write(fd 3 = /dev/ptmx, 4096) (wchan wait_woken, ctx switches frozen);
  both client sockets ESTABLISHED with Recv-Q 65562 (never read again), so new clients were never served.
- mk20-kbd (pid 2936) healthy, polling /dev/pts/0 (fd 6) every 100 ms with POLLIN, json_in/jpg_ok frozen.
- A 2 s read of /dev/pts/0 returned 0 bytes: the slave line discipline buffer was EMPTY while the master write was
  blocked on a full input flip buffer -> the kernel (5.4.61) pty input path stalled (flip buffer not pushed to n_tty).
Recovery (20:52): static diagnostic `ptyflush /dev/pts/0` = tcflush(TCIFLUSH) on the slave -> bridge resumed at once,
old sockets drained/closed, Mac re-hello ok 20:52:20 (page=L1). Diagnostic binary removed from /tmp afterwards.
Conclusion: firmware must self-heal: never block the bridge on the pty master; on a stalled master write flush the slave
input (TCIFLUSH) and drop the client; mk20-kbd flushes its host input after prolonged silence; soak must check
host-link liveness (kbd json_in/jpg_ok progress while a client is connected).
