VERDICT: FAIL

1. **BLOCKER — `deck/mk20deck/controller.py:520-521, 811-817`**  
   Approval fires solely from elapsed time while the press remains in `self.presses`. A dropped key-up, or a loop stall that resumes after the hold threshold, therefore still calls `answer()`. The existing stall test only passes because the queued key-up is drained before `tick()`.  
   **Fix:** invalidate pending approvals on loop lateness or uncertain key state, and require a reliable held-key heartbeat/state before firing; never approve from a stale press alone.

2. **MAJOR — `deck/mk20deck/effects.py:111-125`**  
   SNAP performs `CGPreflightScreenCaptureAccess` and `os.makedirs()` synchronously inside `Daemon._run_effects()`. Filesystem or TCC calls can block the device loop, violating the side-effect isolation requirement.  
   **Fix:** move all SNAP setup, permission probing, and directory creation to a worker process/thread; keep the daemon loop limited to queueing and nonblocking polling.

Verified explicitly: prompt/session/page/mode checks, PTT check, one-shot `approve_done`, reconnect gesture clearing, tmux generation and `pane_in_mode` refusal, argv-only subprocess calls, HID digit removal for approve keys, and Screen Recording permission is not bypassed.