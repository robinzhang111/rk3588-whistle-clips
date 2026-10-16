# C探针首次受控采集核查

2026-10-15。用户提供终端截图，确认已自行按键运行并停止此前授权的一次独立探针。助手仅只读检查、独立下载和分析，没有再次采集、调整参数、校时、安装、部署或运行模型。

## 结论

**独立探针的短时采集、配置读回、状态记录及用户停止释放子任务限定通过；C真实采样UTC与精度仍未通过。** 请求LINK=2，5755条状态全部实际返回COMPAT=0，未获得精度报告。不能将valid=true解读为LINK/ADC时间能力通过，不能生成正式UTC标签。

## 实测结果

原板端会话：`/home/basketclip-project/stage2/outputs/sampling-probe-capture-20261015-01`。

| 项目 | 结果及边界 |
|---|---|
| 配置读回 | 48000Hz、双声道、S16_LE；ENABLED/MONOTONIC |
| 音频时间请求 | LINK=2、report_delay=true |
| 实际返回 | 5755/5755条为COMPAT=0；valid=true；accuracy_report=false、accuracy_ns=null |
| 原始计数 | 每声道累计191504帧，按名义48000Hz为3.9896667秒；逐次读取8～256帧，累计值与summary一致 |
| 本会话时长 | 3.994871437秒，user_stop；用户提前停止，未运行满10秒，不补采 |
| 设备状态 | 5755条状态均RUNNING；本次查询后PCMclosed |
| 异常与释放 | error=null、cleanup_error=null、resources_released=true、segment_invalid_reason=null |
| 连续观测 | 应用计数、外部单调时钟、状态系统时间戳及audio时间戳均未见回退；未见xrun状态。不能据此证明无丢样或长期稳定 |
| 时钟观测窗口 | 用户态UTC/单调夹逼291～55998ns；含状态调用窗口21583～357401ns；均不是UTC或ADC总定位误差 |
| 观测偏移变化 | 本段realtime减monotonic跨度35290ns，未触发5ms诊断门限；不证明NTP绝对精度、采样无漂移或跨设备同步 |

时间类型名称核对的是本次已固定保存的ALSA1.2.8官方`pcm.h`：COMPAT=0、DEFAULT=1、LINK=2。这里只确认实测类型为COMPAT，没有把它改称已返回DEFAULT或精确LINK；驱动内部对应路径和延迟含义仍需只读核查。

首末用户态UTC观测为2026-10-15T02:01:34.924825Z～02:01:38.909150Z（北京时间10:01:34.924825～10:01:38.909150）。这些是应用观测时刻，不是经核验的采样UTC；本次未刷新NTP质量，也未回填历史候选时间。

## 证据保护

本地独立核查目录：`outputs/sampling-probe-capture-review-20261015-01/`。

- `board-originals/`原样保存abi.json、probe-diagnostics.jsonl、summary.json，三文件与板端SHA256逐项一致；板端原件不改。
- `download-verification.json`记录远端路径、哈希、PCMclosed与未重采。
- `inspect.py`只读校验帧累计、状态/类型分布、时钟序列和窗口；新增`inspection.json`，不重写原诊断或summary。
- `user-terminal.png`和`user-evidence.json`保留用户截图及其哈希，截图只作用户操作与终端反馈来源。

探针未保存连续音频、特征或分数，未运行模型。本次没有修改正式源码、16倍增益、FP16、0.65/v1或上传门禁；formal_utc_upload=false。前次64项软件测试和ABI验收属于准备证据，本次短时真实结果单独归档，不能代替长期或完整链路验收。

## 下一项与停止点

停止于这一次结果核查，不重采、不请求其他时间戳类型或自动改驱动。剩余缺口：COMPAT实际时间语义和FIFO/codec延迟、样本位置对应时刻、UTC配对/同步误差、48kHz到正式22050Hz位置关系及漂移。真实采样UTC、D正式上传及E实际录像映射继续待验。

下一项留在04C/04，先只读核查COMPAT的软件估计路径及原样本到重采样输出的位置关系，形成最小方案与误差预算；不因LINK未取得便直接扩展为驱动改造，也不将软件估计自动升级为verified_sampling。后续实施/实测范围需另行明确。

可复制交接提示词：

```text
继续C采样时间下一步，只读核查和方案准备。
先读阶段二C采样时间探针首次受控采集核查.md、任务清单与验证记录，
以及outputs/sampling-probe-capture-review-20261015-01的inspection.json和board-originals。
一次实采191504帧/3.994871437秒、5755条RUNNING，用户停止并释放，原件哈希一致。
请求LINK=2但实返COMPAT=0且无精度报告；探针运行通过不等于采样UTC通过。
核查COMPAT在当前ALSA/内核/驱动中的时间语义、avail/delay与原样本位置关系，
并检查现有FFmpeg持续重采样的48k到22050位置/延迟证据，提出最小软件估计路线与误差预算。
不新采集、不扫时间戳类型、不校时安装部署、不改驱动或正式检测链。
保持16倍/FP16/0.65/v1、relative_only与上传禁用，不追填旧UTC；报告方案后停止。
```
