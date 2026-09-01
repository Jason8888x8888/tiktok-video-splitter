# Contributing

感谢改进 TikTok Video Splitter。

## 开发流程

1. 只修改当前问题需要的范围。
2. 不提交真实视频、Cookie、API Key、签名 URL 或用户路径。
3. 新增行为必须补充回归测试。
4. 运行：

```bash
python3 -m unittest discover -s "tests" -v
python3 scripts/verify_release.py
```

## Pull Request 要求

- 说明问题、方案和用户可见变化。
- 标明是否涉及上传、费用、凭据、Cookie、下载策略或输出 schema。
- 不兼容的 schema 变化必须更新 `schema_version`；兼容的新增字段也必须同步 JSON Schema、测试和 CHANGELOG。
- 不得加入绕过平台登录、地区、验证码、访问控制或下载限制的逻辑。

## 测试素材

只提交可公开再分发的合成 fixture。涉及真实 TikTok 的问题应提供脱敏元数据或最小复现步骤，不提交原视频。
