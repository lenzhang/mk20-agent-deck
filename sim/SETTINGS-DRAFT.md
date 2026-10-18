# On-device settings page — DRAFT (simulator only)

Status: design draft for review, implemented as a state-only clickable mock in `deck/mk20sim/settings.py`.
No real network/model configuration is touched. Screenshot: `sim/screenshot-settings.png`.

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
   "Mac/网页" key and a hint: API keys and Wi-Fi passwords are set from the Mac or a local web page
   (`http://172.30.250.1:8780`, placeholder).
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
Entry (draft): on **L4 (模式/设置页) a short K1 press opens settings** (K1 long press already = go to L4;
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
- **设备信息**: fw 1.1.4, kernel 5.4.61 #221, IP (ECM) 172.30.250.2, GD32 V2.32, current egress (read-only; mock values).

Changes are staged (`pending`) and only take effect with **应用** — avoids a stray knob turn changing the
live network profile.

## 4. Cases (`deck/mk20sim/cases/settings.json`)
open/exit from L4 · switch provider without key → web hint + apply + test fails "无 Key" ·
K2 focus + K1 port adjust + two-press discard · Wi-Fi + foreign provider → test "需代理".

## 5. Open questions for the user
- Entry point: L4 K1 short press OK, or dedicate an L4 key (e.g. replace 回L1 with 设置 since K2 also goes back)?
- Should 应用 on network profile require a confirm (it can cut the agent's model access)?
- Brightness/volume (stock has them via voice/knob) — add as a 6th tab or put under 设备信息?
