# Proxy / Claude preflight — 2026-10-17

## ccai check
- glm/grok/kimi/flash endpoints: HTTP 200
- AI group (global, not SG session): US-Exit-Hy2 / 203.0.113.10 Los Angeles US

## Exit IP contrast
| Path | IP | City | Country | Org |
|------|----|------|---------|-----|
| Direct (ipinfo) | 203.0.113.10 | Los Angeles | US | AS3257 GTT |
| Direct (ipify) | 154.36.145.60 | — | — | — |
| :7895 SG proxy (ipinfo) | 116.14.13.40 | Singapore | **SG** | AS9506 Singtel |
| :7895 SG proxy (ipify) | 116.14.13.40 | — | — | — |

## Verdict
SG dedicated entry :7895 exits in SG. Safe to launch `ccai sg`.
Expected: country==SG via http://127.0.0.1:7895 (matches ccai sg_exit_check).
