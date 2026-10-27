# mac-test on 4eb998f: 1 failure diagnosis (2026-10-27, iMac)

Run: `make mac-test` on the iMac (Intel i5-8600T, macOS 15.8), tree ~/mk20-build/wt-ncm3 (= 4eb998f;
mk20d and test_mk20d_agent.py byte-identical to the branch), log ~/mk20-build/ncm/mac-test-4eb998f.log.
Result: `1 failed, 829 passed, 603 skipped in 1009 s`.

## Failing test

`tests/test_mk20d_agent.py::test_agent_backoff_and_healthy_reset`, line 277:
`wait(lambda: log.read_text().count("retry in") >= 9, 25)` timed out (needs 9 backoff lines within 25 s real time).

## Verdict: environment / timing budget, not a 1.8.0 (NCM) regression

Evidence:
1. **Deterministic on this host**: 5/5 isolated re-runs fail at 25.1 s; each time only 5 "retry in"
   lines are written (delays 1,2,4,8,16 — correct so far).
2. **Same failure on the 1.7.1-rc1 base** (459da10, pre-NCM tree via git archive): fails identically at 25 s.
   Between 459da10 and 4eb998f, mk20d/mk20lib changed only 13/10 lines (composite_up = ncm.usb0 || ecm.usb0),
   and test_mk20d_agent.py and mk20-agent-child.sh did not change.
3. **Logic is correct**: with only the wait raised to 400 s (temporary copy in ~/mk20-build/ncm/diag/180,
   not committed) the test **passes in 57.5 s** with all assertions, delays exactly
   `[1, 2, 4, 8, 16, 30, 30, 1, 2]`, so the >=60 s healthy reset works, and rc=7 and agent-crash.times are present.
4. **Linux suite is green on the same code**: test-full-2aa7668.log (Linux VM, BusyBox) reports 1431 passed,
   which includes this test.

Root cause: on Darwin the fixture replaces `cut`/`sleep`/`wc`/`tar`/`flock`/`pgrep` with Python helper
scripts. Each call costs about 0.13 s of interpreter start-up on this Intel iMac (measured: 10 starts = 1.28 s),
and system load was about 3-5 (the 1.8.0 soak plus the full suite). Every virtual second of backoff runs
`web_mode` (several helper forks) plus `sleep` (a helper), so it costs about 0.4-1 s real. The 9 retries
need about 94 virtual ticks, which is about 55 s real, more than the 25 s budget. The test was evidently
tuned on Linux/BusyBox, or on the faster Apple-silicon dev-mbp.

## Suggested fix (not applied)

Test-only: scale the wait on Darwin (for example 25 -> 120 s when /proc/uptime is absent), or make the
mocked `sleep`/`cut` shell builtins/functions instead of Python helpers. No firmware change is needed.
The mac-test result for 4eb998f is otherwise green.
