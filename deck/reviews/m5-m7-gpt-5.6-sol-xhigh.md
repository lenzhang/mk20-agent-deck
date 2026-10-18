Three release blockers found.

BLOCKER
- `deck/firmware/release/share/gadget.sh:231-238` — flapmon and the 180-second confirmation waiter can independently invoke rollback. The waiter ignores the stopfile, so a flap rollback can be followed by a second rollback that tears down the restored stock gadget. The waiter’s `sh -c` also interpolates `$LOG` and `$MK20_REL` unsafely. Use one argv-safe `request_rollback` path with a generation-scoped atomic once-marker, stop both waiters before rollback, and test that racing triggers increment a rollback counter exactly once.
- `deck/firmware/RECOVERY.md:3,109-112`, `deck/tools/tf-restore-boot.sh:40-60` — the documented `MK20_V1.0.img` is a packed IMAGEWTY/PhoenixCard image, but `--whole-image` raw-dds it. This destroys the card and will not produce a bootable system. The listed `live-221-parts/` is also a directory, not a whole-card image. Remove both from the raw-dd instructions; document PhoenixCard/OpenixCard for the vendor image, require a verified full-card raw image, and have the tool enforce exact destination size plus expected SHA.
- `deck/tools/mk20_accept.py:136-142`, `deck/tools/mk20_soak.py:64-68`, `deck/firmware/RECOVERY.md:22` — the Shift down/up writes are not atomic. If down is queued/consumed and polling stops before up, killing the writer can leave Shift pressed. Soak also violates its read-only requirement by doing this hourly. Remove HID writes from soak and use `--no-hid` for the documented read-only recovery check. For acceptance, once down may have completed, keep a supervised key-up writer queued until completion; never kill it.

MAJOR
- `deck/firmware/release/share/flapmon.sh:14-24` — `suspended -> not attached` is ignored, so unplugging while suspended never counts as a real detach. Track a suspension episode: direct resume and `default -> addressed -> configured` are exempt, but `not attached` or a reset sequence that does not return to configured within a grace period counts once. Add these cases to `tests/test_flapmon.py:41`.
- `deck/tools/mk20_accept.py:35-47,132`, `deck/tools/mk20_soak.py:98-140` — soak can still wedge or exit without a final summary: missing `MK20_ADB` raises uncaught `SystemExit`, `hidutil` has no timeout, timed-out wrapper pipelines can leave child `adb` processes, and summary writes are outside `finally`. Use a process-group-aware bounded runner, time out every subprocess, raise normal exceptions, and atomically finalize the summary in `finally`.
- `deck/firmware/release/share/flapmon.sh:20-21` — rollback status is discarded and the monitor exits success; if command startup fails, `gadget.busy` remains stale and no monitor retries. Make `--rollback-now` return a meaningful verified result, clear ownership markers on failure, and perform a bounded retry/failsafe.

MINOR
- `deck/firmware/release/share/gadget.sh:229-230` — unset `MK20_REL` now guarantees failure after USB has already been changed, despite the script retaining a non-release mode. Resolve a companion flapmon path or reject non-release operation during preflight.
- `deck/tools/mk20_soak.py:114-116` — every historical `last_rollback` is reported as “seen” during this soak. Snapshot the initial value and record only transitions, preferably with timestamps/counts.

The new flapmon `"$@"` invocation itself is correctly quoted and BusyBox-ash compatible.
done rc=0
