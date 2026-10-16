# C真实采样时间：只读核查与最小方案

2026-10-14。本轮只读读取本地接口/日志、板端时钟/库/头文件和官方文档。没有打开PCM、采集、校时、安装、修改运行源码或部署；本文件仅为独立方案材料。

## 当前证据

- 板端date为2025-06-27T02:59:02Z，与当前2026-10-14不符；timedatectl synchronized=no。ntpq四项pool均reach0/stratum16；rv为leap_alarm、sync_unspec、peer0、refidINIT，没有可用同步证据。系统与RTC日期都不可信，本轮没有修复。
- PCM状态/hw_params为closed，没有打开设备探测时间戳能力。此前arecord日志tstamp_mode=NONE，只有tstamp_type=MONOTONIC名称，不能据此认为曾获得有效时间戳。
- 现有FFmpeg5.1 ALSA demuxer用户选项只有sample_rate/channels。当前输出f32le pipe只有音频字节，不向run_segment提供原始PTS/采样时间。日志start数值不能直接当作已核验ADC首样本UTC。
- libasound.so.2存在，时间戳配置、系统htstamp、audio_htstamp、report、avail/delay函数符号存在；gcc/cc/pkg-config可用，/usr/include/sound/asound.h存在，但alsa/pcm.h缺失。存在函数不证明ES8388/当前驱动支持精确link/analog采样时间，也不证明调用能在采集条件下正常返回。
- sample_time.SamplingAnchor/SegmentTime已有纯软件转换、缺口与时钟失效门禁。当前board_runtime仍创建无锚点SegmentTime，所以relative_only正确。必须提供原采样位置、时钟/UTC依据、原始到22050Hz位置对应关系和不确定度；16倍增益不改变样本位置或时间关系。

## 官方接口核对

[FFmpeg5.1 ALSA源码](https://ffmpeg.org/doxygen/5.1/alsa__dec_8c_source.html)在读取音频后，用系统时间减去排队样本delay及本次读取长度，并经过timefilter生成PTS。因此它是软件估计，不能仅靠当前日志start作为真实硬件时间证据；官方5.1源码解释行为，但本次没有读取Debian补丁源码，不声称逐行验证已安装二进制。

[Linux ALSA时间戳说明](https://www.kernel.org/doc/html/latest/sound/designs/timestamping.html)区分trigger、系统、DMA及link/analog时间。请求不支持的硬件类型可能回退到DEFAULT/DMA，驱动返回类型/精度必须检查。启用时间戳不自动解决FIFO/codec延迟，也不能单凭系统与单调时钟一致证明样本连续或无漂移。

## 最小分步方案（尚未实施）

1. **先解决UTC来源，单独授权变更。** 只读检查板端/电脑到可用时间源的实际连接和既有NTP状态，再报告最小同步修复。需校时或改配置时另得授权；不能从SSH收到命令的时刻硬设毫秒精度，也不靠显示日期正确验收同步。记录来源、偏差/同步状态及后续变化，精度目标由验收用途确定。
2. **先做采样时间能力探针，不动正式16倍检测入口。** 电脑本地编写/模拟验证一个小型ALSA时间诊断适配，优先使用标准库ctypes调用现有libasound，避免为了探针安装新音频库或开发头文件。受控实机采集必须另授权、用户按键开始/停止。记录原48kHz样本累计位置、同一状态快照的系统/单调时间、avail/delay、音频时间戳类型/有效性/精度报告、xrun/重启。只是候选技术路线，ABI和调用正确性仍需本地/实机验证；不将库符号存在当作能力验收。
3. **取得依据后最小接入已有C接口。** 保留现有FFT/NPU/16倍/0.65/v1。先核对原始采集样本到当前FFmpeg持续重采样输出样本的关系、滤波/FIFO延迟与误差；按连续区间建立锚点。不能简单假设22050Hz输出样本0等于48kHz原样本0。若选择先保留FFmpeg PTS，仍只标待核验软件估计，不提升verified_sampling；若数据不足则继续relative_only。
4. **受控验收之后才解锁正式标签适配。** 验证起点/中段/末端的时间关系、所需误差、中断/时钟跳变/采样不连续时锚点失效与新段重建。DEFAULT/DMA回退及未测延迟必须如实纳入误差或判待验。校时、采样时间和重采样关系各自有证据后，才把真实锚点传给SegmentTime，继而另验D保存/上传与E实际录像映射。

## 验收和停止点

目前只读方案核查完成，C真实UTC未通过，正式上传禁用。最先可开展的是UTC来源只读诊断和C能力探针的本地软件实现；本次没有执行。正式PCM时间戳能力不能在完全不打开采集设备的条件下确认，后续需明确受控采集授权。

即使板端UTC修正，旧相对候选也没有自动得到历史采样锚点；不追填旧event_time_utc。相机原片2019元数据和屏录的文件时间仍不能直接用于跨设备录像定位。识别效果/增益优化保持后置，时间正确性是功能必需条件，不能用候选已生成代替。
