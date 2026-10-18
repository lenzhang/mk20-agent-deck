# Codex verification notes (Mac, 2026-10-17 ~09:10–09:17 CDT)

Proxy precheck (`.grill/recheck.sh … codex` + strict check), before every codex run:
`AI.now=US-Exit-Hy2 exit=203.0.113.10 US · PASS` (×3). mihomo `/connections`
during the run: every openai.com / chatgpt.com connection chain = `US-Exit-Hy2 ← AI`.

Read-only source (`probe-codex`): ~27 ms per poll, 13 Desktop threads.
Finding: Codex compresses inactive rollouts to `<rollout>.jsonl.zst`, so
`threads.rollout_path` can point at a missing file → handled as cold/idle.

Hooks (codex-cli 0.162.1, `~/.codex/hooks.json`, logging-only probe in `tools/hooks/`):
`codex exec --dangerously-bypass-hook-trust` fired SessionStart → UserPromptSubmit → Stop
within ~1.4 s; `session_id` == thread id, `turn_id` present, `transcript_path` == rollout path.
Exec threads are `source=exec, originator=codex_exec` → excluded from the Desktop list.
Desktop sessions need the hook to be trusted in the App (Settings → Hooks) first.

Deep link `open codex://threads/<id>`: accepted by the running App (it rewrote
`.codex-global-state.json`), but the screen was locked (front app = loginwindow), so
focus could not be measured. Side effect observed: the App rewrote
`heartbeat-thread-permissions-by-id[<id>]` (danger-full-access → read-only) for the
opened thread. Unread-set write latency was not observable without a Desktop turn.
