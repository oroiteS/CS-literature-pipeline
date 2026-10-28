# literature-pipeline

**An agent skill for literature tracking**: give it a research direction, it returns a few fresh, relevant, open-source papers — arXiv first, whitelisted conferences as fallback, with a ledger so no paper is ever delivered twice.

一个「论文追踪」agent skill：按你指定的研究方向检索论文，**arXiv 优先 → 会议兜底**，硬门槛卡**会议白名单**和**开源代码**，带台账保证**同一篇论文绝不推送两次**。

## 它做什么

```
① arXiv 检索（近 N 天）→ 三层去重
② 三道门槛：综述/benchmark → 无开源 → 会议不在名单（任一不过即淘汰）
③ 排序：venue_score × relevance/10
④ 交付 1–5 篇精简卡片
⑤ 归档：pushed.csv（永久去重）+ 当天日报
⑥ arXiv 不够 → 会议渠道补足
```

**它不做什么**：不管调度（cron/手动由你决定）、不做六维评分、不落笔记库、不做精读长文。

## 安装

把本仓库克隆为 agent 宿主的 skill 目录即可，目录名保持 `literature-pipeline`：

```bash
# Claude Code（用户级）
git clone https://github.com/oroireS/literature-pipeline.git ~/.claude/skills/literature-pipeline

# Claude Code（项目级）
git clone https://github.com/oroireS/literature-pipeline.git .claude/skills/literature-pipeline
```

其他 agent 宿主按其 skill 约定放置。skill 内脚本只用 Python 标准库，无需安装依赖。

## 首次使用

skill 里**没有预设的方向和会议名单**，需要你提供。首次调用时 agent 会一次性问全：

| 要问的 | 说明 |
|---|---|
| 关注方向 | 一句话描述 + 3–8 个英文关键词 |
| arXiv 类目 | agent 按方向提议，你确认（不预设"你一定是计算机方向"） |
| 放行会议名单 | A 会 + 你额外放行的 B 会，可带别名 |
| 会议年份窗 | 如 2025–2026 |
| 补充排除词 | 可选 |

答案写入 `references/profile.yaml` 和 `references/venues.yaml`。**配置不全时拒绝运行**，不会拿空名单跑出 0 篇假装正常。

## 核心规则

| 规则 | 实现 |
|---|---|
| 只看白名单会议 | `references/venues.yaml` 唯一真源，别名匹配 |
| 会议分数一致、可改 | `default_score` + 单会 `score` 覆盖 |
| 纯 preprint | 放行，但分数折半（0.5 vs 1.0） |
| 无开源不放行 | 硬门槛，含仓库判空 |
| 已推送不重发 | `pushed.csv` 永久去重 |
| 被拒论文不重复评估 | `seen.csv`，30 天后自动复核 |

## 目录

```
├── SKILL.md                    # 主流程与规则
├── manifest.yaml               # 按需加载索引
├── references/
│   ├── profile.yaml            # ★ 研究方向（访谈生成）
│   ├── venues.yaml             # ★ 会议白名单（访谈生成）
│   ├── venue-gate.md           # 综述/benchmark + 会议门
│   ├── opensource-gate.md      # 开源门槛
│   ├── arxiv-channel.md        # arXiv 检索与降级
│   ├── conference-channel.md   # 会议兜底
│   ├── digest-format.md        # 卡片模板
│   └── state-files.md          # 台账 schema
├── scripts/
│   ├── arxiv_search.py         # 抓取 + 解析 + 去重
│   └── dedup.py                # 台账读写
└── data/
    ├── pushed.csv              # 已发送（永久）
    └── seen.csv                # 已评估（30 天滚动）
                                # data/daily/ 运行时自动创建，存每日日报
```

## 依赖

- Python 3.10+（用 `urllib` 标准库，无需第三方包）
- 可选 `pyyaml`（没有则用内置极简解析兜底）
- `curl`（agent 直接抓取时用）

## License

[MIT](LICENSE)
