# Kimi K3 — devtest HID-leak gate r2 (70c7cc5), 2026-10-22 07:36 — GO

• VERDICT: GO

  **旧问题复核**
  - BLOCKER 跨步骤归因（旧 devtest.py:780):FIXED。`_classify` 先归因后豁免，±0.35s 全局（devtest.py:884-887)，测试 `test_hid_rule_cross_step_leak_is_attributed_not_exempted`。
  - BLOCKER PTT 窗口丢全部类型（旧 :813,820-824):FIXED。PTT 窗（±0.3s）仅豁免 flagsChanged，同窗错类型入 `wrong_type`→fail(devtest.py:864-873, 895-897, 929)；参数化测试覆盖错类型/他键泄漏。
  - MAJOR hello≠fw≥1.5≠live(devtest.py:43-53, 401-429):FIXED。`bottom_row_fw` 要 ui.hidKeys 能力或 x.y.z≥1.5.0;`_live` 要该步前后普通键 CDC（拔线前）；无证据→unconfirmed→manual，永不 pass。
  - MAJOR live PTT 必 warn(devtest.py:973-995):FIXED。`_ptt_status` 接受"仅 flagsChanged"，经 `_settle`(:850-855）用后续步骤证据终审；完整 1.5.x 跑测试全 pass（本地 43/43 通过）。
  - MINOR 测试拆分：FIXED(deck/tests/test_reconnect_devtest.py:604-760，隔离用例齐全）。

  **新发现（均 MINOR)**
  1. devtest.py:884-887：快用户在上一普通键释放后 <0.35s 按底排键，合法 Backspace 被跨步骤归因判 fail（测试用 0.45s 延迟回避）。方向安全但会误报；建议 fail 文案提示"若紧邻上次按键请放慢重测"，或仅对 press 边归因。
  2. devtest.py:889-902：延迟 >0.35s 且类型相符的泄漏落入允许步骤仍计入 `expected`（无计数上限）,PTT 窗内同类 flagsChanged 泄漏亦不可辨——类型级监视的固有盲区，建议 limitation 注明。
  3. devtest.py:763, 772-773：交互模式（无 HID 监视器）底排键恒 unconfirmed→manual，即便用户已在文本窗目睹输入；建议交互模式加一句"底排是否各输入 ⌫/⏎/Esc"的人工确认以转正。

  固件证据链（onboard.c:334 能力自报、627-660 live 页转发/吞边）与实现一致，未发现可伪造成 pass 的路径。

