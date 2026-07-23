# AI Agent Radar

每天发现 Codex、Claude Code、Grok、Kimi、MCP 与 Agent Skills 项目，生成中文日报和周榜。核心排行无需模型，所有条目均保留原始链接和入选原因。

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
GITHUB_TOKEN=your_read_token .venv/bin/ai-agent-radar daily
GITHUB_TOKEN=your_read_token .venv/bin/ai-agent-radar weekly
```

生成命令默认是 dry-run，只写 `data/` 和 `reports/`，不会创建 Issue。实时采集只允许使用配置时区的当天日期；`--date` 主要用于当天的确定性重跑，不能把当前 GitHub 数据伪装成历史快照。

发布采用两阶段流程：先生成并把报告提交、推送到仓库，再显式发布已经存在的文件：

```bash
GITHUB_TOKEN=your_write_token GITHUB_REPOSITORY=owner/repo \
  .venv/bin/ai-agent-radar publish daily
GITHUB_TOKEN=your_write_token GITHUB_REPOSITORY=owner/repo \
  .venv/bin/ai-agent-radar publish weekly
```

兼容写法 `ai-agent-radar daily --publish` / `weekly --publish` 也只发布已有报告，不会重新采集。这样 Issue 不会领先于仓库中的持久化报告。

## 数据源与配置

编辑 `config/radar.yaml` 可以增删查询组、RSS/HTML/GitHub Releases 官方来源、排除项、榜单长度、评分权重和 `quality` 质量策略。`feeds[].kind` 可以是 `rss`、`html` 或 `github_releases`；HTML 来源只收录同时具有链接和机器可读发布时间的 `article`，GitHub Releases 来源读取官方仓库的结构化发布数据。非空但结构失效的资讯响应会标记为来源失败，不会伪装成健康空源；响应采用流式读取并在 5 MB 处中止。

质量门槛会排除空壳、镜像式 fork、关键词堆砌和无关内容；有近期可验证 Release 的归档仓库、具备独立提交证据的 fork，以及配置中的官方组织/可信主题可以按证据保留。系统只读取 API 元数据，不克隆或执行候选项目。

## GitHub 配置

- Secret `MODEL_API_KEY`：可选；缺失时自动使用模板摘要。
- Variable `MODEL_BASE_URL`：可选，默认 `https://api.openai.com/v1`。
- Variable `MODEL_NAME`：可选，默认 `gpt-5-mini`。
- Actions 自带的 `GITHUB_TOKEN` 用于读取 API、提交报告和更新 Issue。

日报使用 `radar-daily` 标签，周榜使用 `radar-weekly` 标签。同一日期重复发布会更新已有 Issue；所需标签会自动幂等创建。

## 调度

日报 cron 为 UTC `0 0 * * *`，对应北京时间每天 08:00；周榜 cron 为 UTC `30 0 * * 1`，对应北京时间每周一 08:30。可在 Actions 页面选择对应工作流并点击 **Run workflow** 手动运行。每个工作流都会先生成报告，把 `data/` 和 `reports/` 的变更提交并推送，然后才更新 Issue；降级运行会先持久化状态，再以非零状态结束且不发布 Issue。

## Telegram 日报

日报成功生成并发布 GitHub Issue 后，会向一个 Telegram 私聊发送完整的
`reports/daily/YYYY-MM-DD.md` 附件、摘要和 GitHub 报告链接。生成或此前工作流
步骤失败时只发送简短告警，不附带报告内容。周榜暂不发送 Telegram。

在 BotFather 创建 Bot 后，先打开该 Bot 的私聊并发送 `/start`。推荐在本地运行
安全配置脚本；Token 使用隐藏输入，两个值通过 stdin 写入 GitHub Actions
Secrets，不会保存到文件或显示在命令参数中：

```bash
.venv/bin/python scripts/configure_telegram.py \
  --repo apiiskan/ai-agent-radar
```

脚本会设置：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

如果 Token 已经单独设置但不知道 chat ID，可在 Actions 页面手动运行
`AI Agent Radar Daily`，勾选 `telegram_test`。测试只发送一条私聊消息，不运行
日报；消息内会显示完整 `TELEGRAM_CHAT_ID`，Actions 日志只显示掩码。随后把该
数字添加为同名 Repository Secret。

完成配置后，手动运行一次未勾选 `telegram_test` 的日报工作流。Telegram 应收到
一个以当天日期命名的 Markdown 文件；下载后可与仓库中的日报逐字节比较。若未
收到消息，先在 Actions 日志确认是生成、Issue 发布还是 Telegram 步骤失败，再
确认 Bot 未被停用、两个 Secrets 名称正确，并重新向 Bot 发送 `/start`。若
`getUpdates` 报告 webhook 冲突，需要先移除该 Bot 的 webhook 再运行配置脚本。

## 评分

综合分 = 45% 热度增长 + 25% 实用性 + 20% 新鲜度 + 10% 主题相关性。模型不会修改该分数。

周榜的“新上榜/掉榜/名次变化”按相邻完整 Top 20 快照计算；“连续升温”至少需要 4 个完整日期快照，“7 日增长”需要对应基线。历史不足或 GitHub 发现不完整时，报告会明确显示数据不足并抑制榜单迁移结论。

## 降级与排错

- GitHub 接近限流时会停止低优先级详情请求，并用已有数据生成降级报告；不完整库存不会用于判断项目消失或掉榜。
- 单个资讯源失败时，报告的“来源状态”会显示来源名和错误类型，其他来源继续运行。
- GitHub 发现不完整或所有主要来源都失败时，生成命令返回 1；配置/日期错误返回 2。报告和来源状态会在返回前写入，已有完整快照不会被全失败空结果覆盖。
- 模型不可用时自动使用模板摘要，不影响排名和报告。
- 当天可直接重跑生成命令；历史报告应从已保存快照渲染，不支持用实时 API 回填过去日期。发布命令更新同标题 Issue，不会重复创建。

## 安全

系统不会克隆或执行候选仓库代码。密钥只从环境变量读取，`.env`、原始 API 响应和临时缓存均不会提交。
