# TikTok Video Splitter

> 长视频素材堆在一起，真正麻烦的不是下载，而是逐镜切开、编号、抽帧、核对时间线，再整理成剪辑软件能批量使用的素材包。

TikTok Video Splitter 把用户明确提供的一条 TikTok 视频或本地 MP4 拆成连续物理分镜，生成同名关键帧、总览图、CSV 和可追溯 JSON。默认全程本地、零 Token；只有用户明确授权上传和费用时，才使用模型逐镜补充语义名称。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 你会得到什么

```text
视频拆解-示例视频-1234567890-2026-08-31/
├── 源视频.mp4
├── 分镜总览.jpg
├── 01_分镜视频/
│   ├── 001_物理分镜_00m00s-00m04s.mp4
│   └── 002_物理分镜_00m04s-00m08s.mp4
├── 02_关键帧/
│   ├── 001_物理分镜_00m00s-00m04s.jpg
│   ├── 002_物理分镜_00m04s-00m08s.jpg
│   └── 分镜总览.jpg
└── 03_索引记录/
    ├── candidates.json
    ├── 分镜资产清单.csv
    ├── segments.json
    ├── download-summary.json
    └── run-summary.json
```

运行摘要会记录场景事件是否被截断、镜头密度、人工复核建议、工具版本、输入哈希、Token 使用和失败阶段。它不会把“结构校验通过”伪装成“语义已经人工核验”。

## 安装

克隆仓库后，在仓库根目录执行：

```bash
npx skills add .
```

确认可发现：

```bash
npx skills add . --list
```

也可以把当前 `TikTok-video-splitter` 文件夹复制到 Agent 支持的 Skills 目录。

## 前置条件

- [ ] Python 3.10+：运行 `python3 --version` 验证。
- [ ] FFmpeg 与 FFprobe：macOS 可运行 `brew install ffmpeg`，Ubuntu 可运行 `sudo apt-get install ffmpeg`。
- [ ] yt-dlp：仅处理 TikTok URL 时需要，推荐使用其[官方安装方式](https://github.com/yt-dlp/yt-dlp/wiki/Installation)。
- [ ] curl：仅 `hybrid` 语义模式需要。
- [ ] Ark API Key：仅 `hybrid` 模式需要；本地模式不读取、不上传、不计费。

## 如何触发

你可以直接对 Agent 说：

- “把这个 TikTok 视频按物理分镜拆开，默认本地零 Token。”
- “把这条本地 MP4 整理成混剪素材包，生成关键帧和 CSV。”
- “我授权上传并承担费用，请在物理分镜不变的前提下补充语义命名。”

下载-only、账号监控、批量抓取、固定时长机械切割、字幕提取、叙事分析和最终成片剪辑不会触发本 Skill。

## 先做只读预检

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/video.mp4" \
  --output-parent "/absolute/path/output" \
  --validate-only
```

预检不会联网或创建输出目录。它会返回输入、输出、工具和凭据的机器可读能力矩阵；`status=invalid` 时退出码为 2。

## 两种模式

### 本地基础拆解

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/video.mp4" \
  --output-parent "/absolute/path/output" \
  --mode local
```

- 完全本地处理。
- Token 固定为 0。
- 文件名基于客观时间范围。
- 语义置信度记录为“不适用”，不会伪造为 100%。

### AI 语义拆解

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/video.mp4" \
  --output-parent "/absolute/path/output" \
  --mode hybrid \
  --api-key-file "/absolute/path/ark-key.txt"
```

- 完整视频会上传到火山方舟并产生费用。
- 模型只给物理分镜逐一命名，不能更改边界。
- 默认上传限制为 200 MiB；预计模型输出超限时提前停止。
- API 未返回 Token usage 时记录为未知，不会错误记成 0。

## 安全与隐私

- yt-dlp 强制 `--ignore-config`，不会偷偷继承用户全局下载参数。
- FFmpeg、FFprobe、yt-dlp 和 curl 使用最小子进程环境，不继承 Ark API Key。
- 来源 URL 会移除查询参数、片段和嵌入式凭据。
- 外部工具错误会脱敏 API Key、URL 查询参数和用户主目录。
- `render-only` 拒绝绝对来源路径与目录穿越。
- Cookie 必须由用户明确授权，Skill 不读取或转述 Cookie 内容。

## 输出兼容性

视频会重编码为 MP4/H.264/yuv420p；存在音频时使用 AAC。该项目只承诺“剪映/CapCut 兼容编码配置”，当前 Beta 尚未把真实编辑器导入作为自动化验收，因此不宣称“已通过 CapCut 导入认证”。

## 故障排查

| 问题 | 处理方法 |
|---|---|
| 预检返回 `status=invalid` | 查看 `checks` 中 `required=true` 且 `ok=false` 的项目，补齐输入、工具或凭据。 |
| TikTok 下载失败 | 更新 yt-dlp；确认链接可访问。Skill 不会绕过登录、地区、验证码或平台限制。 |
| 出现 `.partial` 目录 | 查看其中 `03_索引记录/run-summary.json` 的 `stage` 和 `next_action`。已有合法 `segments.json` 时可重新渲染。 |
| `events_truncated=true` | 说明候选事件超过上限；检查总览图并人工复核，必要时明确调整 `--max-scene-events`。 |
| 混合模式被大小限制阻止 | 先确认隐私、费用和服务端限制，再明确调整 `--max-hybrid-upload-mb`；不要静默绕过。 |
| 分镜看起来过密或过疏 | 查看 `quality.reasons`，再针对素材类型调整 `--scene-threshold`，并保留调整记录。 |

## 开发与验证

```bash
python3 -m unittest discover \
  -s "tests" -v
python3 scripts/verify_release.py
```

代码只依赖 Python 标准库；运行链路依赖的外部工具在 `requirements.txt` 中说明。

## 项目状态

当前版本：`0.1.0-beta.1`。

Beta 已覆盖本地合成视频端到端链路和安全回归测试。真实 TikTok 下载会受平台与 yt-dlp 变化影响；真实剪映/CapCut 导入和混合模式人工语义质量基准仍是升到 `v1.0.0` 前的门槛。

## 致谢

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)：TikTok 链接下载与元数据解析。
- [FFmpeg](https://ffmpeg.org/)：视频检测、转码、抽帧与总览生成。
- [Volcengine Ark](https://www.volcengine.com/docs/82379)：可选语义标注服务。
- [Agent Skills](https://agentskills.io/)：Skill 包格式。

## 许可证

[MIT](LICENSE)
