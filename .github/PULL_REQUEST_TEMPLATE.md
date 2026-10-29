## 改了什么 / What

## 为什么 / Why

## 怎么测的 / Testing
- [ ] Linux Docker `make test` 通过
- [ ] macOS `make -C deck mac-test` 通过（只允许 CONTRIBUTING 里列出的已知计时失败）
- [ ] 真机验证（固件 / 设备行为改动必填：版本、步骤、结果、照片或日志）
- [ ] UI 改动附了前后截图 / 更新了金标帧

## 设备影响 / Device impact
- [ ] 不涉及设备
- [ ] 涉及设备侧行为（说明回滚方式）：

## 安全清单 / Safety checklist
- [ ] 没有向 GD32 发送 `0x19` / `02 AA E2 78`，没有刷写 GD32
- [ ] 没有在设备上使用 `reboot`（只用 `cmd-reboot` / `hardreset.sh`）
- [ ] adb 只经 `mkadb.sh`
- [ ] 没有提交密钥、内网地址、个人路径、大文件或厂商资料
