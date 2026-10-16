# 合成示例

`events.synthetic.jsonl` 是格式与计算测试用的合成事件，不来自真实比赛。

可在一个时长超过 25 秒的测试视频上使用，先建立示例映射：

```text
python -m basketclip align --session demo --event-utc 2026-10-10T12:00:00Z --video-seconds 0 --output outputs/demo-alignment.json
```

再用自己的测试视频执行截取。此过程仅验证工具链，不能证明真实时间同步。
