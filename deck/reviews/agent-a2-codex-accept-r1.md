FAIL

- **MAJOR — [rfb.c:325](/Users/dev/worktrees/mk20-agent-a1/deck/agent/src/rfb.c:325):** Snapshot deadlines are checked only during socket I/O. An 8192×8192 solid ZRLE frame compresses to approximately 89 bytes; once buffered, decoding and scaling process 67 million pixels without checking the deadline and can return success after expiry. Check the deadline between tiles/rows and before finalization. Add a regression using a highly compressed frame; the current timeout test only delays delivery.

Items 1–8 otherwise have host implementation coverage. No additional defects found in DES/auth, armhf-sized arithmetic, bounded streaming memory, JPEG cap/fallback, password handling, or clipboard refusal/discard. Device §14.5 measurements remain pending as documented.

C11 warning-clean syntax checks passed. Rebuilding/tests were blocked by `/tmp` write EPERM; the reported 130-pass run was not independently repeated.