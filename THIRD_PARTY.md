# Third-party components / 第三方组件

本仓库自有代码采用 [MIT 许可证](LICENSE)。下列第三方组件**不**适用 MIT，各自保留原许可证：

| 组件 | 位置 | 版本 / 来源 | 许可证 |
|---|---|---|---|
| Mbed TLS | `deck/agent/vendor/mbedtls/` | 3.6.7 LTS（见 `deck/agent/vendor/VENDOR-A3.md`） | Apache-2.0（`deck/agent/vendor/mbedtls/LICENSE`） |
| zlib（inflate 子集） | `deck/agent/vendor/zlib/` | 1.3.1（`VERSION`） | zlib（`deck/agent/vendor/zlib/LICENSE`） |
| cJSON | `deck/agent/vendor/cjson/` | 1.x | MIT（`deck/agent/vendor/cjson/LICENSE`） |
| stb_image_write | `deck/agent/vendor/stb/` | nothings/stb `2c980bb`（`VERSION`） | MIT 或 Public Domain（双许可，见文件尾部） |
| stb_image | `deck/kbd/vendor/stb_image.h` | v2.30 | MIT 或 Public Domain（双许可，见文件尾部） |
| Mozilla CA 证书包 | `deck/agent/vendor/ca/cacert.pem` | curl.se 提供（见 `VENDOR-A3.md`） | MPL-2.0 |
| DejaVu Sans Mono 字形（ASCII 位图） | `deck/kbd/ui_font.c` | DejaVu | Bitstream Vera / DejaVu（`deck/kbd/font-LICENSE-DejaVu.txt`） |
| Linux 内核模块 `usb_f_ncm.ko`、`usb_f_ecm.ko` | `deck/firmware/release/lib/` | Linux 5.4.61（Tina） | GPL-2.0；对应源码见 `deck/kernel/gpl/` |
| Linux 内核补丁 / 源码 | `deck/kernel/` | Linux 5.4.61（Tina） | GPL-2.0（`deck/kernel/gpl/LICENSES/GPL-2.0`） |
| GNU C Library（静态链接） | `deck/firmware/release/bin/*` | Debian trixie `libc6-dev-armhf-cross`（见 `deck/tools/build-image/Dockerfile`） | LGPL-2.1-or-later |

## 关于 `deck/firmware/release/bin/` 里的预编译程序

`mk20-agent`、`mk20-kbd`、`mk20-router`、`ttygs_tcp_bridge` 由本仓库源码交叉编译，并**静态链接** GNU C Library（LGPL-2.1+）。
按 LGPL 要求：这些程序的完整源码和构建脚本都在本仓库中（`deck/tools/build-image/`），任何人都可以修改后重新编译、重新链接；
glibc 源码可从 Debian（`apt-get source glibc`，trixie）或 <https://ftp.gnu.org/gnu/glibc/> 获取。

## 依赖但不随仓库分发

- Python 依赖通过 pip 安装，不随仓库分发，许可以各包为准。
- 设备厂商的原始固件、示例代码与产品照片不随仓库分发；`deck/firmware/release/share/vendor.sha256` 只记录厂商文件的哈希，用于校验。

## ⚠️ 待处理

- `deck/kbd/ui_font.c` 中的 **CJK（中文）位图字形**由 macOS 自带的 STHeiti Light 光栅化生成（见文件头）。STHeiti 是 Apple / SinoType 的专有字体，
  不是开源字体。公开发布前应改用开源字体（例如 Noto Sans CJK / 思源黑体 OFL，或 OFL 像素字体）重新生成，并同步更新 `mk20-kbd` 二进制与 UI 金样图。
