# C探针：独立受控采集准备验收

2026-10-15。用户授权本地补齐、独立探针部署与一次最长10秒的受控采集；实际采集由用户按键开始。当前已完成本地及板端准备，**尚未实际采集**。此前56项状态读取器测试记录保留，本次增加完整句柄/配置/采集生命周期。

## 本次实现

- 新增`tools/sampling_capture.py`，自持一个非阻塞采集句柄。固定原始S16_LE/48000Hz/双声道/RW_INTERLEAVED，禁用ALSA软件重采样，设置后读回实际格式，不匹配则停止；不改变mixer或正式输入增益。
- 显式启用时间戳并请求MONOTONIC，应用软件参数后读回模式/类型。`sampling_probe.AlsaStatusReader`新增可选audio_config，原调用默认不变；每次状态请求前指定LINK和report_delay，保留实际返回类型/有效性/精度，不自动改请求重试。
- 1024帧有界原声缓冲，只累计每声道实际返回帧数；短读正常累计，EAGAIN仅等待数据。音频读取后丢弃，不写连续录音、不接FFmpeg重采样/FFT/NPU。保留同一状态快照的系统/audio/driver时间戳、avail/delay与时钟域；应用读取边界不是ADC位置锚点。
- UTC/单调时钟用用户态夹逼观测，保存原始纳秒值及状态调用窗口，不将两者称为原子配对。5,000,000ns为本次固定诊断跳变阈值，**不是5ms采样或UTC精度承诺**，不启用verified_sampling或上传，不追填旧候选。
- 非RUNNING、读取/状态/写证据失败或时钟跳变使段失效并停止。没有recover、自动重开、参数回退或再次采集。用户停止、10秒循环限时、Ctrl+C均进入资源释放；关闭失败单列且结果不能判通过。
- 必须先完成ABI检查，再等用户第一次空格；首次空格以前不打开PCM。再次空格停止，程序也会按10秒采集时长上限结束。取消等待不开始采集。

## ABI依据与验证

从ALSA官方HTTPS获取`alsa-lib-1.2.8.tar.bz2`，仅在电脑证据目录提取`include/pcm.h`，不安装开发包。来源和归档/头文件SHA256存`source.json`。头文件SHA256为`b9fd5ca72a12731efd401b0902674c23c5a5fe212b2723e200124b074877f458`。

`verify_abi`从此固定头文件提取config/report原始声明，使用板端既有gcc和系统time.h编译临时类型自检，不链接ALSA或打开PCM。C结果与目标Python ctypes结果逐项比较；临时程序完成后清理。原生结果：指针/long8、unsigned int4、timespec16/tv_nsec偏移8、report8/accuracy偏移4、config4；位域样例字节report=`3500000078563412`、config=`12000000`，全部匹配。该检查证明类型布局；不证明驱动支持LINK或采样精度，也不等于完整Debian补丁源码审计。

板端只读确认ALSA1.2.8、必需符号存在。正式设备关闭，不能在不开PCM的条件下读回下一次生效的时间戳配置；这仍属于用户接下来运行的受控采集内容。

## 验收证据和部署

电脑证据目录：`outputs/sampling-probe-capture-prep-20261015-01/`。

- `tests.log`：8新增采集测试＋此前56项，共64项通过。覆盖非阻塞/短读计数、限时/用户停止、配置读回拒绝、逐状态音频类型请求、xrun/读取/状态/写失败、Ctrl+C、清理错误和无音频不判通过。
- `local-verification.json`：64项结果及上传文件哈希，原7份核心源码与03会话5份证据保护哈希一致。`sample_time`、正式启动器/输入源/预测器/模型/16倍/0.65/v1未修改。
- `board-abi.log`和`board-abi.json`：板端原生ABI检查返回结果。
- `board-preflight.json`：板端返回的上传哈希/版本/符号/PCMclosed/尚未采集结果。上传5份内容文件与电脑SHA256逐项一致，另上传清单自身，共6份。

独立部署目录：`/home/basketclip-project/stage2/sampling-probe-20261015-01`。只新增此目录，不覆盖runtime-20261014-02、模型/环境或历史数据。上传前确认PCMclosed且无相关采集进程；上传后ABI/哈希检查不打开PCM。没有校时、软件安装或模型运行。

## 用户执行一次受控采集

在MobaXterm的板卡终端执行以下整条命令；输出目录必须不存在。不要并行启动正式采集或其他录音程序。

```bash
python3 -B /home/basketclip-project/stage2/sampling-probe-20261015-01/sampling_capture.py --header /home/basketclip-project/stage2/sampling-probe-20261015-01/upstream-pcm-1.2.8.h --output /home/basketclip-project/stage2/outputs/sampling-probe-capture-20261015-01 --seconds 10 --clock-tolerance-ns 5000000
```

看到“ABI通过。空格开始一次采样探针”后按一次空格。正常环境即可，不要求播放哨声；本次只检查采样与时间状态。再次空格提前停止，或等待最多10秒。若报错保留现场和本会话输出，不重跑、不换设备或参数。

结果仅包含`abi.json`、`probe-diagnostics.jsonl`和`summary.json`，无WAV、特征或逐窗分数。完成后用户反馈“C探针采集完成”及终端结果，助手再独立下载原文件、核对两端哈希、查询PCMclosed并审查返回类型/时间序列/失效。当前尚无这些实采结果。

## 下一门槛

已完成本地软件与独立部署准备，等待用户按键采集。实际配置、连续帧/状态、LINK回退、reported accuracy和退出释放须以本次真实结果分别判定；功能运行成功不代表真实采样UTC通过。后续原48kHz到正式22050Hz的位置关系、FIFO/codec延迟、UTC误差与漂移仍需独立证据，正式上传继续禁用。

完成后留在04C/04核查结果，不自动进入正式检测链或D/E。可复制续接提示词：

```text
核查已授权的一次C探针受控采集，先读阶段二C采样时间探针受控采集操作与准备验收.md及任务/验证记录。
会话为/home/basketclip-project/stage2/outputs/sampling-probe-capture-20261015-01。
只读检查原结果与设备释放，独立下载新目录并核对两端哈希，报告配置读回、样本计数、返回时间戳类型/精度、时钟观测窗口和中断失效。
不重跑、不换参数、不校时安装，不把LINK/valid或reported accuracy当作已核验ADC UTC，不追填旧标签。
保持正式16倍/FP16/0.65/v1和上传禁用；结果不足则保留现场，停止于本次核查。
```


## 2026-10-15后续实采状态

用户已自行按键完成这一次会话并提前停止。191504帧、5755条状态、资源释放；请求LINK但实际COMPAT且无精度报告。真实UTC未通过，不再执行上方采集命令。最新结果与停止点见[C首次实采核查](./C采样时间探针首次受控采集核查.md)。
