# 使用板载 REC 按键记录时间标签

## 已核实的按键来源

- 本地资料：`D:\Study\野火\鲁班猫_RK3588\硬件资料\LubanCat-RK系列硬件资料集合_20260617\hardware\LubanCat-RK_选型手册_规格书_硬件资料\鲁班猫5\[野火]鲁班猫5开发板硬件规格书_V1.1.4.pdf`。
- PDF 第 25～26 页（印刷页码 24～25），第 4.5.2 节：PWR、MR、REC、RST 四个按键，REC 具有上电 Recovery 烧录用途。
- 实机 Linux 输入枚举：设备名称 `adc-rec-keys`，当前 `/dev/input/event4`，EV_KEY 键码 250。
- 程序按设备名称查找，不固定 event4 编号；找不到 REC 设备时不替换成电源按键。

读取范围仅为按键相关手册段落和实机输入设备，不代表已精读全部硬件资料。原始资料未修改。

## 操作条件

系统已经正常启动且 SSH 可连接时短按 REC 并松开。不要按 PWR、MR、RST；不要按住 REC 再上电。程序不修改设备树、GPIO 方向或系统按键配置。

本程序运行期间独占 REC 输入设备，避免相同事件被桌面重复处理；退出时释放。不改变按键上电阶段的硬件用途。

## 板卡终端命令

在 MobaXterm 的板卡终端执行：

```bash
cd /home/basketclip-project/stage1
/usr/bin/python3 -u -m basketclip record \
  --board-button \
  --session rec-001 \
  --output data/rec-001/events.jsonl \
  --alignment-output data/rec-001/alignment.json \
  --video-seconds 0
```

看到“已监听……adc-rec-keys”后，在板卡上短按 REC 即可生成一个标签。终端不用输入 m 或回车。按下产生事件，松开与自动重复事件不记录。结束时在终端按 Ctrl+C，程序保存的文件保留，退出码 130 表示人为中断。

每次使用新会话和目录，例如将所有 `rec-001` 改为 `rec-002`；文件默认拒绝覆盖。

当前模拟约定：程序开始对应录像第 0 秒，记录多久后按键就映射到录像大约第几秒。标签不代表已有录像内的真实哨声。选用录像时长应覆盖事件位置。

## 时间记录方法

读取 Linux 输入事件的单调时钟时间戳，结合启动时 UTC 与单调时钟映射生成标签。这样记录的是内核按下事件时间，而非 SSH 文本到达电脑的时间。仍需验证实际按键行为和真实录像时间关联，不能因使用内核时间戳就声称端到端同步合格。

## 导出与截取

板卡文件位于 `/home/basketclip-project/stage1/data/rec-001/`，包含事件与模拟对齐记录。按 [先标签后录像模拟](./先标签后录像模拟.md) 的方法导出，命令中的会话名改为本次实际名称。电脑端 FFmpeg 和真实录像条件仍需确认。
