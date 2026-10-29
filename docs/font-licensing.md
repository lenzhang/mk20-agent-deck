# CJK 字体授权处理

已选择方案 A：设备端 CJK 位图改用 **Noto Sans SC Regular** 生成。字体来自 Noto CJK `Sans2.004` 发布包，采用 SIL Open Font License 1.1；许可证副本保存在 [`deck/kbd/font-LICENSE-NotoSansSC.txt`](../deck/kbd/font-LICENSE-NotoSansSC.txt)。

固定信息：

- 下载包：[`18_NotoSansSC.zip`](https://github.com/notofonts/noto-cjk/releases/download/Sans2.004/18_NotoSansSC.zip)
- 下载包 SHA-256：`4d107c09ada479d3e48b6e78c83835773cbd9214bf6e12cdb7b60f8e068292ec`
- 使用文件：`NotoSansSC-Regular.otf`
- 字体文件 SHA-256：`faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9`
- 生成结果：[`deck/kbd/ui_font.c`](../deck/kbd/ui_font.c)，只包含固定栅格位图，不包含运行时字体文件。

用同版本字体和锁定的 Pillow / FreeType 重新生成：

```sh
python3 deck/tools/gen-ui-font.py \
  --font /path/to/NotoSansSC-Regular.otf \
  --ascii-font /path/to/DejaVuSansMono.ttf
```

`ui_font.c` 的文件头记录了字体、两个源文件的 SHA-256 和栅格器版本；使用 `--check` 可以验证提交的字形表是否过期。生成后需要重新构建 `mk20-kbd` 并运行 `make -C deck/kbd ui-test`，因为 CJK 字形变化会影响 UI 金样图。
