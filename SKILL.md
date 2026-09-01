---
name: tiktok-video-splitter
description: |
  Split one user-provided TikTok video or local MP4 into ordered physical shots, matching keyframes, an overview image, traceable indexes, and optionally a local CapCut draft via cutcli for remix asset preparation. Use when the user explicitly asks for TikTok 视频拆分、物理分镜、视频切片、混剪素材整理、剪映/CapCut 兼容素材或导入 CapCut 草稿。Local mode is offline and zero-token; hybrid mode adds one-to-one semantic labels only after explicit upload and cost authorization. Do not use for download-only requests, account monitoring, batch scraping, fixed-duration chopping, transcription, subtitles, narrative analysis, or final montage editing.
---

# TikTok 视频拆分

把用户明确提供的一条 TikTok 视频或本地 MP4 转成可追溯的物理分镜素材包。Skill 负责“分镜与资产化”，下载仅是处理链接时的准备步骤。

## 边界

- 一次只处理一条用户明确提供的视频，不发现账号、不遍历播放列表、不批量抓取。
- 默认使用“本地基础拆解”（CLI：`local`）：FFmpeg 本地检测、切片、抽帧，模型调用和 Token 均为 0。
- 默认拆解灵敏度为 `--scene-threshold auto`：先本地预扫描视频，不上传、不用模型，再根据真实转场密度、运动强度、候选切点数量和视频时长自动选择阈值与最短分镜时长。
- “AI 语义拆解”（CLI：`hybrid`）只给既有物理分镜逐一命名，不得合并、拆分、重排或修改时间边界。
- 下载失败时不绕过登录、地区、验证码、访问控制或平台限制。
- 下载-only 请求应交给专门下载 Skill；字幕、叙事分析和最终剪辑不属于本 Skill。
- CapCut 草稿同步是可选交付阶段，不是第三种分析模式：只有用户明确要求“导入 CapCut/剪映草稿”或 CLI 显式传 `--capcut-stage draft` 时才执行。

## 安全约束

1. `hybrid` 会上传完整视频并产生费用；只有用户明确授权本次上传和付费调用后才能执行。
2. Cookie 只能在用户明确授权后通过 `--cookies-from-browser` 或 `--cookies-file` 使用；不得读取、记录或转述 Cookie 内容。
3. API Key 只通过 `--api-key-file`、`ARK_API_KEY` 或 `ARK_API_KEY_FILE` 读取。外部子进程使用最小环境变量，不继承 API Key。
4. yt-dlp 强制忽略用户全局配置，所有下载行为必须由本 Skill 显式声明。
5. 默认拒绝覆盖最终输出或 `.partial` 目录；`--replace-assets` 仅用于用户明确授权的重新渲染。
6. `--capcut-stage draft` 会写入本机 CapCut 草稿目录，依赖本机已安装并可执行的 `cutcli`；不得改用 UI 自动点击或要求用户手动导入来替代该路径。

## 开始前

先运行只读预检，不下载、不上传、不创建输出目录：

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/video.mp4" \
  --output-parent "/absolute/path/output" \
  --validate-only
```

预检必须返回机器可读能力矩阵，并检查：输入、输出父目录、FFmpeg、FFprobe，以及按需检查 yt-dlp、curl、cutcli、CapCut 草稿目录、提示词、Cookie 文件和 Ark 凭据。`status=invalid` 时停止。

## 执行

本地基础拆解：

```bash
python3 scripts/assetize_tiktok.py \
  "https://www.tiktok.com/@account/video/1234567890" \
  --output-parent "/absolute/path/output"
```

也可直接处理本地 MP4：

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/video.mp4" \
  --output-parent "/absolute/path/output" \
  --mode local
```

自动灵敏度可显式写成：

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/video.mp4" \
  --output-parent "/absolute/path/output" \
  --mode local \
  --scene-threshold auto
```

自动模式使用本地转场密度和运动强度选择参数，并把证据写入三个 JSON 契约；它是启发式判断，结果异常时应改用手动阈值。算法与覆盖边界见 [references/auto-tuning.md](references/auto-tuning.md)。

本地模式使用客观名称，如 `001_分镜_00m00s-00m04s.mp4`；语义置信度为“不适用”，不能伪装成 `1.0`。

AI 语义拆解：

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/video.mp4" \
  --output-parent "/absolute/path/output" \
  --mode hybrid \
  --api-key-file "/absolute/path/ark-key.txt"
```

默认阻止超过 200 MiB 的混合模式上传，可用 `--max-hybrid-upload-mb` 在用户明确知情后调整。模型预计输出超过 `--max-tokens` 时停止，不静默截断。

同一视频比较模式时复用已下载文件，并校验 SHA-256：

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/源视频.mp4" \
  --reuse-download-summary "/absolute/path/03_索引记录/download-summary.json" \
  --output-parent "/absolute/path/another-version" \
  --mode hybrid \
  --api-key-file "/absolute/path/ark-key.txt"
```

只生成方案和关键帧时添加 `--plan-only`。已有 `segments.json` 可在用户确认后重新渲染：

```bash
python3 scripts/assetize_tiktok.py \
  --render-only "/absolute/path/03_索引记录/segments.json" \
  --replace-assets
```

需要直接写入本机 CapCut 草稿时，在素材包生成命令后添加：

```bash
python3 scripts/assetize_tiktok.py "/absolute/path/video.mp4" \
  --output-parent "/absolute/path/output" \
  --mode local \
  --capcut-stage draft
```

可用 `--capcut-draft-name "草稿名"` 指定草稿名。该阶段会调用 `cutcli draft create` 和 `cutcli videos add`，创建 1080×1920 草稿、复制分镜视频到草稿 `Resources`，并保持原视频音量为 1。草稿名已存在、缺少 `cutcli` 或本机 CapCut 草稿目录不可写时，素材包仍保留，并在 `capcut-draft-summary.json` 记录失败原因。

模型约束见 [references/shot-label-prompt.md](references/shot-label-prompt.md)，JSON 契约见 [references/schemas](references/schemas)。

## 输出

```text
视频拆解-视频名-视频ID-YYYY-MM-DD/
├── 源视频.mp4
├── 分镜总览.jpg
├── 01_分镜视频/
├── 02_关键帧/
│   └── 分镜总览.jpg
└── 03_索引记录/
    ├── candidates.json
    ├── 分镜资产清单.csv
    ├── segments.json
    ├── download-summary.json
    ├── capcut-draft-summary.json
    └── run-summary.json
```

分镜视频统一编码为 MP4/H.264/yuv420p；存在音频时编码为 AAC。未传 `--capcut-stage draft` 时只能宣称“剪映/CapCut 兼容编码配置”；传入后可宣称“已通过 cutcli 写入本机 CapCut 草稿并生成 smoke summary”，但不能宣称官方认证或跨版本保证。

## 验收

按 [references/quality-gates.md](references/quality-gates.md) 检查时间线、资产一致性、事件截断、资源记录与失败恢复；触发和输出回归位于 [evals](evals)。最终回复链接输出目录、`segments.json`、CSV 和运行摘要，并展示根目录总览图。模型语义标签不是人工核验结果；`quality.human_review_recommended=true` 时必须明确告知用户。
