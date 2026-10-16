# C采样时间探针：本地软件限定验收

2026-10-15。用户授权电脑本地实现与模拟验证；停止在本地软件验收。不连接采集设备、不校时、不安装或部署，不改模型、16倍增益、0.65/v1。此前系统校时基础子项见阶段零记录，本次未现场复核，也未将其当作采样精度依据。

## 实现与接口边界

- `tools/sampling_probe.py`：独立探针，不接入 `board_runtime` 或正式采集链。复用 `sample_time.SegmentTime` 的候选映射、时钟检查和失效接口，以及既有字段校验；不修改原时间接口。
- `AlsaStatusReader(library).read(pcm)`：标准库 ctypes 状态读取适配。调用方显式传入库和已配置的 PCM 句柄；模块不加载库，不打开、配置、读取音频、恢复或关闭设备。一次 `snd_pcm_status` 后从同一状态对象获取 state、avail、signed delay、系统/audio/driver htstamp 和 audio report，始终释放状态内存；分配或状态调用失败明确抛错，不自动重试。
- `SamplingProbe.observe`：记录 segment_id、48kHz等原始采样率、累计原始帧位置、快照、外部观测的单调时间/UTC及其依据、时间戳时钟域/启用状态。帧位置是**每声道的应用读取边界计数**，由调用方提供，不是 ADC 首样本时间，也不能直接用 `read_count+avail/delay` 伪装硬件位置锚点。
- 原始 ALSA htstamp 的时钟域与外部观测时钟分别记录。时间戳值、valid、actual_type、accuracy_report/accuracy_ns 保留；精度无报告时为 null，0不能冒充未知。DEFAULT/DMA回退、返回valid或reported accuracy均不自动提升为硬件采样精度。
- xrun、挂起、断开等非RUNNING状态、原样本计数回退、连续观测间单调回退或墙钟偏移变化均使该段失效。显式缺口/重启调用 `interrupt(reason)`；首个失效原因保留，后续恢复RUNNING不能复活旧段。状态读取异常由调用方停止处理并调用 `interrupt`，本版未接入真实采集调用方。新段重新实例化，默认没有锚点。
- 无锚点仍 `relative_only`。本地版本仅允许显式 synthetic 锚点用于复用映射测试，拒绝 verified_sampling；不根据到达时刻、状态时间戳或NTP状态生成采样锚点。全部诊断 `sampling_anchor_verified=false`、`resampling_relation_verified=false`、`formal_utc_upload=false`，没有四字段标签或上传路径。

ctypes声明按ALSA官方[状态接口](https://www.alsa-project.org/alsa-doc/alsa-lib/group___p_c_m___status.html)和[音频时间戳报告结构](https://www.alsa-project.org/alsa-doc/alsa-lib/structsnd__pcm__audio__tstamp__report__t.html)编写。实库调用限制Linux LP64；本次Windows假库仅验证函数签名声明、字段读写、signed delay、宽计数与释放/错误路径。**没有加载libasound，未验证ARM64 C位域布局、目标板已安装库版本或真实驱动返回能力。** 后续实机之前须核验目标ABI和句柄配置，不能把假库通过当作ABI已验收。

## 验收证据

最终证据：`outputs/sampling-probe-local-20261015-03/`。

- `tests.log`：9项新增探针测试，加8时间、4增益、4空格、13统一入口、5生命周期、6分块、7规则回归，合计56项全部通过。
- `simulation/probe-diagnostics.jsonl`、`simulation/summary.json`：12场景、32条诊断。覆盖无锚点、连续、xrun、挂起、断开、缺口、重启、样本回退、单调回退、墙钟跳变、类型回退及重启新段。10个显式合成映射检查中8个失效后仅相对时间；其余仅synthetic UTC，不产生真实UTC。
- `verification.json`：原时间、统一入口、启动器、输入源、规则、分块、预测器7份源码保护哈希一致；03真实会话4份原文件及已有inspection共5文件哈希一致。测试未运行模型、采集、校时、安装、部署或通信。没有写入原历史输出。
- `verify.py`：本次完整本地验收命令记录；输出独占创建，不能直接复跑到同目录。另建会话目录再运行。

首次证据汇总因Windows默认GBK解码异常停止，`sampling-probe-local-20261015-01`保留为空目录。第二次`-02/tests.log`保留56项中4失败/8异常：既有统一入口在沙箱临时目录 `os.replace` 报WinError5，并引发启动器断言失败。经自动审批允许本地离线测试脱离沙箱重跑，最终56项原样通过；未修改产品源码规避权限问题。两次问题不删除、不冒充通过。

独立重放模拟命令（输出目录必须不存在）：

```powershell
& './阶段2_离线哨声检测/.venv-naodevils-infer/Scripts/python.exe' -B `
  './阶段2_离线哨声检测/tools/simulate_sampling_probe.py' `
  --output './阶段2_离线哨声检测/outputs/sampling-probe-新会话'
```

## 停止点与下一项

本次仅C探针本地软件子任务通过。真实ALSA时间戳能力、UTC配对误差、FIFO/codec延迟、采样漂移/丢样及48kHz到22050Hz实际位置关系均未验收；C整体、正式UTC标签和D/E实机链路仍未通过。旧相对候选不追填UTC，16倍/FP16/0.65/v1保持。

下一项仍在“04C／04｜板卡音频采集与哨声检测”：先只读审查目标ABI、现有采集句柄与时间戳配置的接入方案，再等待独立受控采集授权。本次没有准备或执行设备打开命令，不自动开始下一步。

可复制交接提示词：

```text
继续Project_1 C采样时间探针后续准备，只做只读核查与最小实机方案。
先读AGENTS.md、阶段二任务清单/验证记录、C采样时间探针本地实现与模拟验收.md，
以及outputs/sampling-probe-local-20261015-03/的tests.log、simulation和verification.json。
本地56项测试/12合成场景通过不代表Linux ARM64 ABI或真实ALSA能力通过。
核对目标库/头文件、ctypes布局、PCM句柄归属及时间戳配置、状态失败如何中断失效，
明确原样本计数、同一快照时间域、avail/delay、实际返回类型/有效性/精度的取证方式。
不得用状态/到达时间制造verified_sampling，不假设重采样零延迟，不追填历史UTC。
保持16倍/FP16/0.65/v1与正式上传禁用；不打开设备、不校时、不安装部署，
不启动模型/采集。给出需另行授权的受控采集范围与验收条件后停止。
```
