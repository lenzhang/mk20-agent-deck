BLOCKER — `deck/firmware/release/share/gadget.sh:187-191`  
Setup holds fd 7 after stopping the old flap monitor, but setup and its EXIT cleanup remain unbounded. A hung service script, `setusbconfig`, or add-ADB script permanently blocks rollback with no active monitor to reboot. Run the mutating setup under a bounded supervisor; on timeout kill/reap its tree and perform bounded cleanup before releasing fd 7.

MAJOR — `deck/firmware/release/share/gadget.sh:166-168`  
`MK20_RB_TIMEOUT` is unvalidated after the cleanup child inherits fd 7. Invalid/overflowing arithmetic can terminate the supervisor and orphan a possibly hung lock holder. Validate and cap timeout/lock-try values before locking or forking.

MAJOR — `deck/firmware/release/share/gadget.sh:166-176`  
Timeout kills cleanup before its `rm -f gadget.busy`; direct rollback callers do not clear it. Rollback can return success/failure with a stale busy marker. Clear it unconditionally after `wait`, while still holding the mutex.

MINOR — `deck/tests/test_rollback_guard.py:10`  
The non-greedy extraction stops at the timeout block’s inner standalone `fi`, producing an unterminated outer `if`. Extract using explicit start/end markers or include through the outer rollback `fi`.

Normal concurrent rollback serialization and removal of cross-owner SIGKILL are resolved. No TF/host acceptance files changed in this diff.
