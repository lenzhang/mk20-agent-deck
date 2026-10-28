# Deploy mk20deck on macOS system python (/usr/bin/python3)

Date: 2026-10-28 (UTC+8). Host: iMac (macOS 15.8), LaunchAgent `com.leo.mk20-agent-deck`, deploy worktree `~/mk20-deploy`.

## Why
macOS 15 *Local Network* privacy silently blocks unsigned, downloaded interpreters
(uv-managed CPython 3.13 under `~/.local/share/uv/python/...`) from reaching LAN /
link-local hosts: `connect(172.30.250.2:17890)` fails with `Errno 65 No route to host`
and **no permission prompt is ever shown**. The deck then only logs
`未找到 MK20 CDC 端口，等待接入` forever. Apple-signed `/usr/bin/python3` (3.9.6) is
allowed and connects fine, so the agent runs on system python instead.

## Code changes (branch `fix/deck-py39`, off `feature/ncm-gadget`)
- `mk20deck/config.py`: `tomllib` (3.11+) falls back to `tomli` on 3.9/3.10.
- `pyproject.toml`: `requires-python = ">=3.9"`, dep `tomli>=1.1; python_version < '3.11'`; `uv.lock` re-resolved.
- `tools/hooks/mk20_hooks_merge.py`: `parse_intermixed_args` (argparse <3.12 rejects
  positional `paths` after `--command X`).
- `adapters/codex_desktop.py`: if `?mode=ro` cannot open `~/.codex/state_5.sqlite`
  (macOS system SQLite 3.43 + WAL db with no `-shm`, i.e. Codex closed →
  `unable to open database file` every poll), reopen with `&immutable=1`.
  Seen right after the switch (16:46:11–16:46:43); verified on a copied db without -shm.
- All modules already use `from __future__ import annotations`; `compileall` clean on 3.9.

Test run on 3.9.6 (`tests tests_sim`): 861 passed / 603 skipped / 7 failed before the
hooks fix; remaining non-3.9 issues:
- `test_agent_backoff_and_healthy_reset`: known Darwin timing budget (see
  `reviews/1.8.0/mac-test-4eb998f-failure.md`), also fails on 3.13.
- `test_hook_interrupts_backoff_and_resets_next_delay[*]`: fails identically on 3.13 at
  8fb7041 (pre-existing on the branch, shell-cutout timeout), not a 3.9 issue.
- `test_agent_selftest_never_fails[crashing]`: test-harness only — it writes
  `int(time.monotonic())` from the pytest process and the mock device reads uptime from
  another python process; on macOS python 3.9 `time.monotonic()` is process-relative, so
  the values only agree when both processes are young (passes in isolation). Runtime code
  only uses monotonic differences inside one process (supervisor, power sleep detection).

## Deploy layout
- venv: `~/mk20-deploy/.venv-sys` = `/usr/bin/python3 -m venv`; `pip install "Pillow>=10.1" "pyserial>=3.5" "tomli>=1.1"`
  (Pillow 11.3.0, pyserial 3.5, tomli 2.5.0). pip 21.2 cannot do PEP 660 editable
  installs, so the package comes from `PYTHONPATH=/Users/dev/mk20-deploy/deck` (already in the plist).
- plist `ProgramArguments[0]` = `/Users/dev/mk20-deploy/.venv-sys/bin/python` (was `.venv/bin/python`, uv 3.13).
- Backup: `~/mk20-backup/pre-syspython-20261028/` (old plist, pyvenv.cfg, venv info, config.toml).

## Rollback
```sh
cp ~/mk20-backup/pre-syspython-20261028/com.leo.mk20-agent-deck.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.leo.mk20-agent-deck; launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.leo.mk20-agent-deck.plist
```
(The old uv venv `~/mk20-deploy/.venv` is left untouched, but it cannot reach the LAN.)
