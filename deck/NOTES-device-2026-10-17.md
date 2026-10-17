# MK20 实机记录（2026-10-17 10:05–10:20 CDT / 23:05–23:20 SGT）

只读操作；没有写过设备（没有 VIA 写入、没有 DFU、没有 SHOW_JPG）。

## 枚举
- T113 `1d6b:0104` "Serial"（Allwinner，bcdDevice 0x0504），**只有 CDC-ACM**（没有 ECM 接口）→ `/dev/cu.usbmodem1101`，
  位置 0x00110000，接在机内 GL852G hub（`05e3:0610`）上，hub 直连 Mac 根口 `AppleT6000USBXHCI@00000000`。
  （之前是 `/dev/cu.usbmodem21101`，说明重新插到了另一个口。）`en10` 是另一个 USB 网卡，**不是** MK20 的 ECM。
- GD32 `4250:426f` "syk_keyboards"（SYK，bcd 0x0100，全速）：hidapi 能看到 if0 键盘、if1 VIA `0xFF60/0x61`、
  if2 鼠标/系统/consumer、if3 `0xFF31/0x74`。
- **异常**：GD32 在 30 秒内大约每 1–2 秒重新枚举一次；之后虽然稳定在 `IOUSBHostDevice@00120000`，但没有产品名字符串，
  也没有挂上 HID 驱动（hidapi 枚举为空），说明描述符/HID 请求超时。

## Protocol A
- 写入正常（1 MiB `'0'` 前导约 0.11 s 写完），但发 `getInfo` 后 **T113 完全没有回复**：试过加/不加前导、DTR/RTS 高/低、
  24 秒内每 2 秒重发一次、空闲监听，收到 0 字节。没有其他进程占用端口（lsof 为空，ScreenKey 未运行）。
- 推断：T113 上的 KeyboardDevice 没有在服务 CDC（挂起或反复重启；GD32 反复重枚举和这一点一致）。

## 需要人工
- 把 MK20 断电重插（拔掉 USB-C 等 5 秒再插）。最好插回之前那个口（usbmodem21101 那条链路）或带电源的 hub；
  再看是否稳定：`python3 -m mk20deck usb-list`，然后跑 `devtest --auto`。
- C920 现在对着人，没对着 MK20；要拍设备照片得先把摄像头转过去（拍到人的那张已删除）。
