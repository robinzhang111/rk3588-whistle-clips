# B-Human 2017 音频许可待确认事项

2026-10-11：复核[官方数据集页](https://b-human.de/datasets-en.html)的2017条目，仍只给出下载入口，没有明确音频数据许可。此前官方目录及本地ZIP也未提供许可文件。配套WhistleLab仓库API本轮因限流未返回元数据，不把空响应解释为license=null或无许可。

当前状态仍为“明确数据许可未确认”。软件许可、网站版权文字及公开下载链接不能替代该音频包的明确使用条件。已记录证据，不继续用同样的宽泛检索制造已确认的假象。

有效的下一步是取得数据维护者的明确说明，或找到明确关联该音频包的正式许可文本。下方是可供用户自行询问的草稿，未发送任何邮件或消息。

```text
Subject: License clarification for the B-Human 2017 whistle detection dataset

Hello B-Human dataset maintainers,

I am preparing a local whistle detection project using the dataset published at:
https://b-human.informatik.uni-bremen.de/public/datasets/whistle-2017/

The archive contains ten WAV files and whistledb.json. I could not find an explicit audio-data license on the current dataset page or inside the archive.

Could you clarify the license and attribution requirements for:
1. Local algorithm development and evaluation using the recordings and annotations;
2. Running the resulting detector locally on an RK3588 device;
3. Publishing aggregate evaluation results, and whether derived annotations or short audio excerpts may be shared?

I am keeping the original recordings and annotations unchanged. I would also appreciate confirmation about the last label in channel index 1 of LUnitedvsUPenn20170728Half2.wav: start=37570000, end=376700000. The end exceeds the recording length, while the corresponding channel-0 end is 37670000. I have quarantined the invalid label rather than applying an automatic correction.

Thank you for your help.
```

不填入未确认的作者邮箱，不自动发送。许可说明应保留来源、日期、适用数据版本及授权范围；作者关于异常标签的回复也应另存，不能直接改写原件。
