# 分块WAV接口操作与限定验收

2026-10-13：本地接口与同文件板端FP16 NPU验证通过。范围仅顺序连续WAV分块模拟，不含真实麦克风、实时节拍/丢样、采样UTC或自动标签通信；阶段二整体未完成。

## 实现与复用

核查未发现持续/分块接口。复用continuous_whistle的FFT/静音/1024窗/512步长/整段v1参考、basketball_audio_adapter的固定swr滤波及已验证Lite2调用合同；不改旧算法/工具/原模型或历史结果。

- tools/stream_whistle.py：22050Hz float32顺序块、有界窗口、CPU特征、跨块v1状态和事件回调。
- tools/whistle_predictor.py：固定FP16 NPU模型接口；电脑ONNX CPU仅作参考，二者实际运行证据分开。
- tools/detect_wav_chunks.py：默认NPU的WAV入口，一个持续FFmpeg解码/重采样进程，不对每块重启滤波；正常仅事件/汇总/必要错误日志。显式reference-scores才记录诊断逐窗CSV。
- tools/whole_whistle_reference.py：仅诊断使用的整段同后端参考，复用原特征/v1；加载已有22050Hz float32 WAV入内存，但不保存音频/特征副本，不属于正常模式。
- tests/test_stream_whistle.py：新增6项软件测试，另回归7项原规则，本地/板端均通过。

FP16模型SHA256固定82f165c8434508d026bb3c51e7528f16463b9eb3a9be7e0d8c4bcbb873395130，ONNX参考固定4fcf70a935cdca1a7618480ca9df5375a476bddacf35a8fe03db13617441204f；0.65/v1、实际声道独立/OR、不桥接/不补零保持，无训练或调参数。

## 缓存与时间合同

输入必须按连续采样顺序到达，单块最多4096个目标采样帧；入口默认512，验收另用777/4096验证块边界无关性。核心只接适配后的22050Hz，未来麦克风仍需接有状态的采集/重采样源。

固定1024×声道float32音频窗，每窗后保留512点重叠；单声道4096字节、四声道16384字节。活动事件仅保留首窗、峰值/声道和活跃声道集合，不保存全部窗/分数。另有单块输入上限4096×声道×4字节、临时FFT及FFmpeg/Runtime内部缓冲，16KB不是整机内存上限，未做长期RSS验收。

只处理完整窗；EOF不补零，事件结束到最后完整阳性窗覆盖范围，closure记文件总目标样本数，尾部单列。finish释放窗口。6新增测试覆盖1/127/511/512/777/1024/4096点块的FFT/重叠、跨块事件、单窗过滤/无桥接/阈值边界/多路OR、短静音/EOF、100个4096帧四声道长阳性块容量不增长；弱引用确认处理后的输入块和finish后的窗实际释放。合成测试不是实听/质量证据。

候选仅相对样本/秒数、whistle_candidate/pending，absolute_utc_start=null；没有event_time_utc和正式上传调用，不用文件模拟冒充采样UTC。

## 真实文件验证

证据outputs/chunk-file-20261013-01/acceptance.json。板端新目录/home/basketclip-project/stage2/chunk-20261013-01，复用旧独立NPU环境和模型，无新安装/录音/校时，不覆盖旧data/outputs。

| 输入与平台 | 块长 | 窗×声道/候选 | 对同后端整段参考 |
|---|---:|---|---|
| 电脑ONNX，B01 6.43秒、44100Hz | 512/777 | 275×1 / 2 | 分数差0，0阈值变化，全部事件字段一致 |
| 电脑正常模式，同B01 | 4096 | 275×1 / 2 | 不存分数，事件字节与512模式一致 |
| 电脑ONNX，TestBench 210.781179秒、22050Hz | 777 | 9076×4 / 31 | 分数差0，事件字段一致 |
| 板端FP16 NPU，同B01 | 512 | 275×1 / 2 | 对已有整段NPU差0，事件字段一致 |
| 板端FP16 NPU，同TestBench | 777 | 9076×4 / 31 | 对本次整段NPU差0，事件字段一致 |
| 板端正常模式，同TestBench | 4096 | 9076×4 / 31 | 不存分数，事件字节与777模式一致 |

“0差”指各自同后端分块与整段，不称CPU/NPU逐位一致或新的准确率。B01目标141782帧/未处理尾470点；TestBench4647725帧/尾301点，EOF后保留帧0，最大窗1024。板端短诊断约0.886秒，长诊断42.285秒，长正常37.807秒，含初始化/解码/处理/保存；未按实时节拍投喂，不验收麦克风在线延迟/无丢样。

正常模式实际只有events.jsonl、summary.json、decoder.log，没有新WAV、特征、逐窗CSV/NPY；板端长正常分别18232/1955/0字节。显式诊断的CSV/参考scores.npy单独保留，不混称正常模式。原件和模型hash保持，共享源码/测试hash一致；结果包单独下载，不含音频资产或环境，两端SHA256为fe5161d2885b02b3b9c57a6833e1d9028b4d029d20b50617810af348aa11fb1b。

## 重跑与后续

在上述板端新目录运行，output必须换成不存在的目录：

```sh
../deploy-20261013-01/venv-npu-run/bin/python -B tools/detect_wav_chunks.py --input assets/TestBench.wav --model ../deploy-20261013-01/conversion-onnx-fp16/WhistleNetMk16-fp16.rknn --output normal-new
```

不带reference-scores即正常模式。现有接口没有接ALSA/麦克风队列、输入中断/采样锚点或HTTP上传。下一步依当前授权做短视频共源相对时间闭环；真实采集、实时速度/丢样、长期稳定性、采样UTC和自动通信继续待验收，不能用本次文件模拟代替。
