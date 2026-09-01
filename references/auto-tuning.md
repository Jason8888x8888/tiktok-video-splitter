# 自动灵敏度

`--scene-threshold auto` 先以 `0.15` 本地扫描 FFmpeg 场景分数，再用候选阈值 `0.22`、`0.30`、`0.35`、`0.45` 形成试算。整个过程不调用模型、不上传视频。

策略版本为 `local_transition_density_v2`。转场密度使用 `(候选分镜数 - 1) / 视频分钟数`，避免把视频起点的基线分镜误算成一次转场；预扫描没有事件时固定选择“稳定产品展示”档。选择结果、试算数据和原因写入 `candidates.json`、`segments.json` 与 `run-summary.json` 的 `auto_tuning`。

这是启发式参数选择，不是内容类型分类器。快速闪烁、渐变、屏幕录制或异常编码可能造成过密或过疏；检查总览图后可显式使用 `--scene-threshold 0.30` 等数值覆盖，并用 `--min-shot-seconds` 控制最短分镜。
