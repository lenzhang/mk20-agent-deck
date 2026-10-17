# MK20 备份与恢复手册（P0）

P0 **不写** GD32 固件、不改 T113 系统镜像（PRD §2.3/§4）。下面的工具和步骤是
「改 VIA / 刷机之前先留后路」。**所有写入都要显式参数，默认只演练**；真正执行前
先经用户确认。

| 部件 | 备份 | 恢复 | 默认行为 |
|---|---|---|---|
| GD32 VIA 键位/旋钮/宏 | `mk20deck via-backup` → JSON | `mk20deck via-restore FILE --yes-write-keymap` | 备份只读；恢复默认只列出差异 |
| GD32 固件 | 厂商 bin（sha256 固定，见下） | `mk20deck gd32-flash --image BIN --i-understand-this-reflashes-gd32` | 默认只校验镜像 + `dfu-util -l` |
| T113 系统（TF 卡） | 整卡 `dd` 镜像（用户自己的卡） | `dd` 写回；退路：厂商 `MK20_V1.0.img` | 只给手册，不提供自动命令 |

依赖（Mac）：`pip install hidapi`（VIA）；`brew install dfu-util`（只在刷机时需要）。

## 1. VIA 键位备份 / 恢复（GD32，QMK `4250:426f`）

```bash
cd ~/worktrees/mk20-agent-deck-p0/deck
python3 -m mk20deck usb-list                       # 只读：应看到 4250:426f 与 1d6b:0104
python3 -m mk20deck via-backup                     # → ~/.config/mk20-agent-deck/backups/via-<时间>.json
python3 -m mk20deck via-backup --rows 4 --cols 6   # 矩阵自动识别失败时手动指定
```
- 走 VIA raw HID 协议（usage page 0xFF60、32 字节报文），只发读命令：协议版本、
  层数、逐键 keycode（0x04）、原始键位缓冲（0x12）、旋钮映射（0x14）、宏缓冲（0x0E）。
- JSON 里有：每层每键的 keycode 和名字、旋钮正/反转、宏、原始 EEPROM 缓冲及 sha256、
  协议版本/固件版本。矩阵尺寸靠「逐键读值 ↔ 原始缓冲」一致性识别；不一致会提示。
- 代码里从不发破坏性命令（0x06 键位重置、0x09、0x0A EEPROM 重置、0x0B 跳 bootloader、
  0x10 宏重置），见 `hw/via.py: NEVER_CMDS`。

恢复：
```bash
python3 -m mk20deck via-restore ~/.config/mk20-agent-deck/backups/via-XXXX.json            # 演练：列出差异
python3 -m mk20deck via-restore ~/.config/mk20-agent-deck/backups/via-XXXX.json --yes-write-keymap [--macros]
```
- 协议版本或层数和备份不同就拒绝（不同 VIA 版本 keycode 编号不同）。
- 只写有差异的格子（`set_keycode`/`set_encoder`），每写一项立即回读校验。
- 跟 VIA 网页版一样写入动态键位 EEPROM，立即生效、断电保留。

## 2. GD32 固件重刷（DFU `1eaf:0003`）

已知事实（来自 `/workspace/mk20-research/gd32/syk_keyboards_mk20_plus_via.bin`）：
- 39552 B，sha256 `54cc4910f038154a4a10db4b59973ded9294843e0dadccc37eb1f3d7229098d0`
- 链接地址 0x08002000（初始 SP 0x20000400、复位向量 0x08002239）→ 前 8 KiB 是
  stm32duino/Maple bootloader，DFU id `1eaf:0003`，烧录 alt 2（「Upload to Flash 0x8002000」）。
- 镜像内含 USB 设备描述符 VID/PID `4250:426F`、厂商串 "SYK"、产品串 "syk_keyboards"，
  以及 VIA raw HID 报告描述符（0xFF60/0x61）。
- Mac 上的副本：`~/workspace/mk20-recovery/gd32/`（与 sha256 一起放的）。

步骤（**刷之前先做 §1 VIA 备份**）：
1. `brew install dfu-util`
2. 让 GD32 进 bootloader（**未在本机验证**，按可能性从高到低）：
   - Maple bootloader 上电后会短暂停留（约 1 秒）：拔线 → 先运行步骤 3 的检查 → 插线，
     期间 `usb-list` 会短暂看到 `1eaf:0003`；
   - 如果 VIA 备份里某个键是 `QK_BOOT`（VIA v12 为 `0x7C00`，旧版 `RESET` 为 `0x5C00`），按它；
   - 硬件：拆壳后 GD32 的 BOOT/复位按键或 BOOT0 跳线（未核实）。
   - mk20deck 不会发 VIA 0x0B（跳 bootloader）。
3. 演练（只读：校验 sha256/向量表 + `dfu-util -l`）：
   ```bash
   python3 -m mk20deck gd32-flash --image ~/workspace/mk20-recovery/gd32/syk_keyboards_mk20_plus_via.bin
   ```
4. 确认后执行（等价于 `dfu-util -d 1eaf:0003 -a 2 -D <bin> -R`）：
   ```bash
   python3 -m mk20deck gd32-flash --image …/syk_keyboards_mk20_plus_via.bin --i-understand-this-reflashes-gd32
   ```
5. 回到 `4250:426f` 后跑 `usb-list`，然后用 `via-restore`（先演练）把键位恢复到备份。
   EEPROM 模拟区可能保留，也可能被固件重置成默认值；以演练结果为准。

刷坏 bootloader 本身（DFU 不再出现）只能拆壳走 SWD（ST-Link/J-Link），而且需要一份
bootloader 镜像，我们手里没有 → 不在 P0 范围；刷机前务必确认 DFU 设备可见。

## 3. T113 系统（TF 卡）恢复

事实：`sys_config.fex` 里 `storage_type = 1` → **从 TF 卡启动**，系统全在卡上。

**首选：先给你自己的卡做整卡镜像**（在动 T113 任何东西之前；P0 本身不动 T113）：
```bash
# 关机拔卡，用读卡器接到 Mac
diskutil list                          # 按容量找到卡，例如 /dev/disk6 —— 认错盘会毁数据，务必核对
diskutil unmountDisk /dev/disk6
sudo dd if=/dev/rdisk6 of=~/mk20-backup/mk20-tf-$(date +%Y%m%d).img bs=4m status=progress
shasum -a 256 ~/mk20-backup/mk20-tf-*.img > ~/mk20-backup/SHA256SUMS
diskutil eject /dev/disk6
```
恢复：`diskutil unmountDisk /dev/diskN && sudo dd if=<img> of=/dev/rdiskN bs=4m status=progress`。

**退路：厂商镜像**（box 上：`/workspace/mk20-research/dl/oss/…/OpenSourceLicenseFirware_V1.0/MK20_V1.0.img`）：
- 848451584 B，sha256 `1c9885b292417436b568913531ba17fc3d1ad6a8932cefaaf3d36f3d8e0c66bc`；
  Allwinner IMAGEWTY（PhoenixCard）格式，**不能直接 dd**。
- 官方方法：Windows 上用同包里的 PhoenixCard v4.2.7，以「启动卡」模式烧录。
- Mac/Linux 备选：OpenixCard 先把镜像转成可以 dd 的原始镜像（`OpenixCard -d MK20_V1.0.img`），再 dd。未在本机验证。
- 注意：这是厂商「开源协议版」固件，**不一定与出厂系统完全相同**（ScreenKey/语音功能可能有差异），所以首选还是你自己的整卡镜像。
- 镜像 > 100 MiB，没有自动拷到 Mac；需要时再从 box 传过去或从厂商处下载。

诊断：USB 上出现 `1f3a:efe8`（Allwinner FEL）说明 T113 没从卡启动（卡坏/没插好）。
`usb-list` 会标出来。rootfs 里有 `adbd`，可能可以用 adb 只读查看（未验证）。
