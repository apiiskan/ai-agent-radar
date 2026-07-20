# AI Agent Radar

每天发现 Codex、Claude Code、Grok、Kimi、MCP 与 Agent Skills 项目，生成中文日报和周榜。核心排行无需模型，所有条目均保留原始链接和入选原因。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
GITHUB_TOKEN=your_read_token .venv/bin/ai-agent-radar daily --date 2026-07-20
GITHUB_TOKEN=your_read_token .venv/bin/ai-agent-radar weekly --date 2026-07-20
```

默认是 dry-run，只写 `data/` 和 `reports/`，不会创建 Issue。只有显式添加 `--publish` 才会写入当前 GitHub 仓库。

## 数据源与配置

编辑 `config/radar.yaml` 可以增删查询组、RSS/HTML 官方来源、排除项、榜单长度和评分权重。`feeds[].kind` 只能是 `rss` 或 `html`；HTML 来源只收录同时具有链接和机器可读发布时间的 `article`。

## GitHub 配置

- Secret `MODEL_API_KEY`：可选；缺失时自动使用模板摘要。
- Variable `MODEL_BASE_URL`：可选，默认 `https://api.openai.com/v1`。
- Variable `MODEL_NAME`：可选，默认 `gpt-5-mini`。
- Actions 自带的 `GITHUB_TOKEN` 用于读取 API、提交报告和更新 Issue。

日报使用 `radar-daily` 标签，周榜使用 `radar-weekly` 标签。同一日期重复运行会更新已有 Issue。

## 调度

日报 cron 为 UTC `0 0 * * *`，对应北京时间每天 08:00；周榜 cron 为 UTC `30 0 * * 1`，对应北京时间每周一 08:30。可在 Actions 页面选择对应工作流并点击 **Run workflow** 手动运行。

## 评分

综合分 = 45% 热度增长 + 25% 实用性 + 20% 新鲜度 + 10% 主题相关性。模型不会修改该分数。

## 降级与排错

- GitHub 接近限流时会停止低优先级详情请求，并用已有数据生成报告。
- 单个资讯源失败时，报告的“来源状态”会显示来源名和错误类型，其他来源继续运行。
- 所有主要来源都失败时命令返回 1；配置错误返回 2。
- 模型不可用时自动使用模板摘要，不影响排名和报告。
- 删除错误日期的 `reports/` 文件和对应 `data/snapshots/` 文件后，可用同一 `--date` 重新 dry-run；发布模式会更新而不是重复创建 Issue。

## 安全

系统不会克隆或执行候选仓库代码。密钥只从环境变量读取，`.env`、原始 API 响应和临时缓存均不会提交。
