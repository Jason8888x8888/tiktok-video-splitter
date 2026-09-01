# 质量门禁

完成一次拆解后必须确认：

1. 分镜从 0ms 连续覆盖源视频结尾，无重叠、无缺口；FFprobe 可读取全部产物。
2. 视频、关键帧、CSV 和 `segments.json` 的编号与文件名一致；两张总览图 SHA-256 一致。
3. 检查 `events_truncated` 和 `quality.human_review_recommended`；任一为真时提示人工复核。
4. 检查磁盘估算、模式、模型与 Token。混合模式缺少 provider usage 时必须记录 `unknown`，不能记为 0。
5. 失败时读取 `.partial/03_索引记录/run-summary.json` 的 `stage`、`recoverable` 和 `next_action`；已有合法 `segments.json` 时才使用 `--render-only` 恢复。
