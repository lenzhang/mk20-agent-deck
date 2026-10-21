# On-device settings page — DECIDED (v1, 2026-10-19)

Status: **decided** (user delegated the open questions, 2026-10-19). Implemented in the real controller:
model/persistence/apply interface `deck/mk20deck/settings.py`, renderer `deck/mk20deck/render/settings.py`,
page `LS` in `controller.py`. Persistence: `SettingsStore` JSON (device `/data/mk20/settings.json`, Mac
`~/.config/mk20-agent-deck/settings.json`, sim/tests temp; override `MK20_SETTINGS`). Apply goes to a
`SettingsApplier`; the default `LoggingApplier` only logs — no real network/model change yet. Screenshot: `sim/screenshot-settings.png`.

## 1. What the stock Waveshare system does (research)
Sources: Waveshare wiki https://www.waveshare.com/wiki/MK20 (sections "Software Settings",
"XiaoZhi AI Application → 1. Device Networking", "Theme editing", "Dual System Architecture"),
SpotPear mirror https://spotpear.com/wiki/MK20-Mechanical-Keyboard-0.85inch-LCD-2.8inch-Secondary-Screen.html,
CNX article https://www.cnx-software.com/2025/10/29/waveshare-mk20-macro-keyboard-features-twenty-mechanical-keys-with-0-85-inch-color-lcd-keycaps/.

- **There is no real on-device settings menu in stock.** All configuration is done in the host app
  ScreenKey (Windows/macOS): gear icon (top right) → "Device Networking" → pick a 2.4 GHz SSID, type the
  password on the computer, wait for "Connected". The USB Wi-Fi dongle must be inserted before power-on.
- On the device itself the user only sees what the theme draws: key faces (128×128), the 2.8" sub-screen
  (428×142) and two **"Encoder" regions of 214×142** — i.e. the sub-screen is split into a left half for K1
  and a right half for K2, each labelled with what the knob does. Status text such as
  `xiaozhiAI connect / Captcha / Listening / Pausing` is shown in a multi-line text control on the sub-screen.
- Device brightness/volume are adjusted by voice (XiaoZhi built-in commands) or by a knob bound in the theme.
- Waveshare's own monitor OSD (Dual Monitor wiki) uses the classic pattern: wheel = move cursor,
  wheel press = confirm, power key = exit — the same convention we adopt for K2.

I found no photos/videos of an on-device settings screen; the stock screenshots that exist are the
ScreenKey host app (wiki images `MK20_Software_edid_*.jpg`, `MK10_Software_edid_*.png`).

## 2. Design principles taken from stock
1. **Secrets never on device** (stock types the Wi-Fi password in the host app). Our page shows a
   "Mac/网页" key and a hint: API keys and Wi-Fi passwords are set in the keyboard's own web page
   (`http://172.30.250.2:8780`, opened in a Mac browser; see `deck/SETTINGS.md`).
2. **Sub-screen split into K1 / K2 halves** (stock "Encoder 214×142" regions) for permanent knob hints.
3. **Wheel = move, press = confirm** (Waveshare OSD convention): K2 rotate moves the focus ring through
   the current list, K2 press confirms; K1 rotate adjusts the focused numeric value; K1 press shows the
   current summary.
4. Keys are big touch targets: anything visible can also be chosen by pressing its key directly.

## 3. Layout (doc col,row)
```
row1  [网络] [模型商] [模型] [连接测试] [设备信息]   tabs (current tab highlighted)
row2  item1  item2  item3  item4  item5              items of the tab (✓ = selected value,
row3  item6  item7  item8  item9  item10              blue ring = K2 focus, grey = read-only)
row4  [返回 L4] [应用 n项] [撤销] [Mac/网页] [回 L1]
sub   title · summary (*未应用) · message/toast | K1 hint | K2 hint
```
Entry: on **L4 (模式/设置页) a short K1 press opens settings** (K1 long press already = go to L4;
L4 has no free key). Exit: 返回 → L4, 回 L1 → L1 (both discard pending changes; 返回 with pending changes
needs a second press).

Tabs:
- **网络**: Mac 代理 (default) / 板载 Wi-Fi (needs dongle); 代理端口 (K1 adjusts, default 7890);
  Wi-Fi SSID → "Mac/网页设置"; 国内直连 = 自动 (mihomo rules split domestic providers, read-only).
- **模型商**: Qwen (default), GLM, Kimi, DeepSeek marked 国内; OpenAI / Anthropic / Gemini marked 需代理;
  "无Key" badge if no key on file → choosing it hints to set the key via Mac/web.
- **模型**: list for the chosen provider (example names; real list fetched from the provider later).
  Switching provider auto-selects that provider's first model.
- **连接测试**: one key; mock result rules: no key → 失败：无 Key; foreign provider on 板载 Wi-Fi → 失败：需代理;
  else 成功 · simulated latency.
- **设备信息**: 亮度 / 音量 (K1 ±10 %), fw 1.1.4, kernel 5.4.61 #221, IP (ECM) 172.30.250.2, GD32 V2.32, current egress (read-only; mock values).

Changes are staged (`pending`) and only take effect with **应用** — avoids a stray knob turn changing the
live network profile.

## 4. Cases (`deck/mk20sim/cases/settings.json`)
open/exit from L4 · switch provider without key → web hint + apply + test fails "无 Key" ·
K2 focus + K1 port adjust + two-press discard · Wi-Fi + foreign provider → test "需代理".

## 5. Decisions (2026-10-19)
- **Entry = short K1 press on L4** (K1 short press on L1 keeps the effort read-out).
- **Network-profile changes (profile, proxy port) need a second 应用 press** ("将切换网络出口… 再按一次「应用」确认");
  any other input disarms the confirm. Other changes apply on one press.
- **Brightness & volume live on the 设备信息 tab** (first two items, K1 ±10 %, staged + 应用).
