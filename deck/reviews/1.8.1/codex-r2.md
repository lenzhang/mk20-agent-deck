VERDICT: PASS

- **R1-1:** FIXED. Approval requires continuous tick observation, recent frame ACK, and no intervening input; stalls cancel safely.
- **R1-2:** FIXED. SNAP permission, filesystem, capture, clipboard, and timeout work run in the worker thread; daemon polling is locked and nonblocking.
- **New findings:** None.

The documented residual—firmware losing a key-up while the link remains otherwise responsive—is acceptable under the stated protocol constraint; Mac-side detection is impossible without firmware support.