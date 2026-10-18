# Boot images (not in git — too large / device-specific)

Built on box 2026-10-18 ~11:07 SGT from MangoPi Tina linux-5.4.61 + shipping ikconfig.

| File | sha256 | Where |
|---|---|---|
| boot-unmodified.img | afc7c70435e5c15c826c0702e89ceb1cf11355b33471f8a083305627b688b199 | box `kernel-build/out/`, Mac `~/mk20-backup/tf/` |
| boot-patched.img | 59c47e2b194e20eee4416eef94146ba3cafc6e23ee5b12ec8faa4af3c3f014b4 | same (ep3→INT + ECM) |

Lunch expects sha file body: `<hex>  boot-new.img` (see `*.sha256.lunch` if present).
