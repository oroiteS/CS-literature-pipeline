# CS-literature-pipeline

**An agent skill for computer-science literature tracking**: give it a research direction, it returns a few fresh, relevant, open-source papers — arXiv first, whitelisted CCF venues (conferences or journals) as fallback, with a ledger so no paper is ever delivered twice.

一个面向**计算机领域**的「论文追踪」agent skill：按你指定的研究方向检索论文，**arXiv 优先 → CCF 会议/期刊兜底**，硬门槛卡 **venue 白名单**和**开源代码**，带台账保证**同一篇论文绝不推送两次**。

**适用范围**：主要针对计算机领域——arXiv 按 `cs.*` 等类目检索；venue 名单一般从 **CCF 推荐目录**（会议/期刊均可）挑选。CCF 名单不内置、不分 A/B/C 等级：只要在你给的名单里就一视同仁，放行范围完全由你决定。

## 它做什么

```
① arXiv 检索（近 N 天）→ 三层去重
② 三道门槛：综述/benchmark → 无开源 → 已发表 venue 不在名单（任一不过即淘汰；纯 preprint 可放行）
③ 排序：相关度降序，同分按 venue_score 降序，再按时间由新到旧
④ 交付 1–5 篇精简卡片
⑤ 归档：pushed.csv（永久去重）+ 当天日报
⑥ arXiv 不够 → 按用户提供的会议和期刊顺序补足
```

**它不做什么**：不管调度（cron/手动由你决定）、不做六维评分、不落笔记库、不做精读长文。

## 安装

把本仓库克隆为 agent 宿主的 skill 目录即可，目录名保持 `CS-literature-pipeline`（与 `SKILL.md` 的 `name` 一致）：

```bash
# Claude Code（用户级）
git clone https://github.com/oroireS/CS-literature-pipeline.git ~/.claude/skills/CS-literature-pipeline

# Claude Code（项目级）
git clone https://github.com/oroireS/CS-literature-pipeline.git .claude/skills/CS-literature-pipeline
```

其他 agent 宿主按其 skill 约定放置。脚本只用 Python 标准库，无需安装依赖。配置解析支持项目约定的 YAML 子集；不支持的语法会报错，不会静默忽略。

## 首次使用

skill 里**没有预设的方向和 venue 名单**，需要你提供。首次调用时 agent 会一次性问全：

| 要问的 | 说明 |
|---|---|
| 关注方向 | 一句话描述 + 3–8 个英文关键词 |
| arXiv 类目 | agent 按方向提议（计算机方向通常是 `cs.*` 类目），你确认 |
| 放行 venue 名单 | 会议和期刊均可（一般取自 CCF 推荐目录），仅写名称即可；也可补充别名 |
| 会议年份窗 | 扫描会议时提供，如 2025–2026；期刊按在线发表时间筛选 |
| 补充排除词 | 可选 |

答案写入 `references/profile.yaml` 和 `references/venues.yaml`。venue 可只写名称，例如 `venues: [NeurIPS, "Nature Machine Intelligence"]`；需要时也可用带别名的映射。**配置不全时拒绝运行**，不会拿空名单跑出 0 篇假装正常。

## 核心规则

| 规则 | 实现 |
|---|---|
| 已发表论文只看白名单 venue | `references/venues.yaml` 唯一真源，会议和期刊都支持别名；纯 preprint 另行放行 |
| venue 分数一致、可改 | `default_score` + 单项 `score` 覆盖 |
| 纯 preprint | 放行；默认 venue_score 0.5，仅在相关度相同时参与排序 |
| 无开源不放行 | 硬门槛，含仓库判空 |
| 已推送不重发 | `pushed.csv` 永久去重 |
| 被拒论文不重复评估 | `seen.csv`，30 天后自动复核 |

## 目录

```
├── SKILL.md                    # 主流程与规则
├── manifest.yaml               # 按需加载索引
├── references/
│   ├── profile.yaml            # ★ 研究方向（访谈生成）
│   ├── venues.yaml             # ★ venue 白名单（访谈生成，会议/期刊）
│   ├── venue-gate.md           # 综述/benchmark + venue 门
│   ├── opensource-gate.md      # 开源门槛
│   ├── arxiv-channel.md        # arXiv 检索与降级
│   ├── conference-channel.md   # 会议/期刊兜底
│   ├── digest-format.md        # 卡片模板
│   └── state-files.md          # 台账 schema
├── scripts/
│   ├── arxiv_search.py         # arXiv 抓取 + 解析 + 去重（含 --doctor）
│   ├── dedup.py                # 台账读写
│   ├── config.py               # 受限 YAML 解析 + 配置校验
│   ├── doctor.py               # 体检 CLI
│   ├── journal_search.py       # Crossref 期刊候选检索
│   ├── acl_anthology.py        # ACL Anthology 卷/论文链接提取
│   ├── venue_state.py          # 兜底扫描检查点
│   └── venue_review.py         # 名单指纹与复评判定
├── tests/                      # 离线单元/集成测试
└── data/
    ├── pushed.csv              # 已发送（永久）
    └── seen.csv                # 已评估（30 天滚动）
                                # data/daily/ 运行时自动创建，存每日日报
```

## 依赖

- Python 3.10+（使用标准库；无需 PyYAML）
- `curl`（agent 直接抓取时用）

配置后先运行 `python3 scripts/arxiv_search.py --doctor` 离线体检；加 `--online` 可额外探测 arXiv 网络。arXiv 检索 `--max` 是总抓取预算，默认 90，每页至多 30；应检查输出的完整性和截断标记。

会议及期刊兜底由 agent 按 `references/conference-channel.md` 执行，并显式调用检查点工具；目前不是可独立自动运行的全渠道扫描器。

## License

[MIT](LICENSE)
