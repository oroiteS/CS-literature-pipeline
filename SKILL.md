---
name: literature-pipeline
description: |
  Search recent papers for a user-defined research direction: arXiv first, whitelisted-conference fallback,
  hard gates on venue whitelist and open-source code, with a ledger so no paper is ever delivered twice.
  Use when the user asks 找论文 / 搜文献 / 今日文献 / 追踪某方向的新论文 / 有没有新论文.
license: MIT
metadata:
  version: 1.0.0
  tags: [literature, arxiv, search, dedup, conference, whitelist]
---

# Literature Pipeline

按用户指定的研究方向检索论文。

**本 skill 只定义「怎么搜」，不定义「何时搜」。** 调度方式（cron、手动、被其他 agent 调用）由用户在 skill 之外决定，本文档不涉及。

## 0. 启动检查（每次运行第一步）

读 `references/profile.yaml` 与 `references/venues.yaml`，逐项确认：

| 项 | 字段 | 缺失时 |
|---|---|---|
| 关注方向 | `direction.keywords` | 访谈补齐 |
| arXiv 类目 | `arxiv_categories` | 按方向提议 → 用户确认 |
| 会议年份窗 | `conference_years` | 访谈补齐 |
| 放行会议 | `venues` | 访谈补齐 |

**铁律**：方向与名单只能由用户提供。**禁止**自行编造默认名单、默认方向、默认年份顶上跑一遍。缺项 → 说清缺什么、要什么格式，然后停。

一次把缺的项问全，不要分多轮挤牙膏。访谈清单见 `references/profile.yaml` 头部注释。

## 1. 主流程

```
① arXiv 主渠道（永远先跑）
   scripts/arxiv_search.py → 近 N 天候选 → 三层去重（arXiv ID / DOI / 归一化标题）

② 门槛（gate-first：先杀后评，省 token）
   综述·benchmark 门 → 开源门 → 会议门
   任一不过 → 淘汰，写入 seen.csv

③ 排序
   final = venue_score × relevance/10，降序；同分取新
   venue_score：放行会议 1.0（名单可调）· 纯 preprint 0.5

④ 截断交付
   取 final 降序前 max_papers（默认 5）篇；relevance < min_relevance 的一律不交付

⑤ 交付
   精简卡片 → 当前会话（格式见 references/digest-format.md）

⑥ 归档
   pushed.csv 追加（永久去重）· data/daily/YYYY-MM-DD.md 存当天日报

⑦ 兜底：arXiv 交付不足 max_papers → 会议渠道补足
   逐个放行会议取 accepted list → 关键词预筛 → 回查 arXiv 拿 ID/摘要
   → 同一套门槛 → 补到 max_papers 或名单耗尽
```

## 2. 三道门槛

按此顺序执行，命中任一即淘汰，**不进入打分**。

### 2a. 综述 / benchmark 门

主贡献是综述、position paper、benchmark、评测集、数据集发布的，一律淘汰。
判据：标题/摘要以 "we introduce / propose / present … benchmark|dataset|testbed|suite" 为中心贡献，或主产物是排行榜/评测框架。
**不影响**：方法论文顺带做了个评测集（方法为主贡献的不受影响）。
详见 `references/venue-gate.md` §1。

### 2b. 开源门（硬门槛）

没有公开可用代码的，**一律不放行**。

- 先读 abs 页的 `Comments` 字段——代码链接绝大多数在这里。
- abs 页无结论 → 抓 HTML 全文 / 项目页。
- 开源范围从宽：GitHub / GitLab / HuggingFace model·dataset·Space / 官方项目页可下载代码。
- **仓库必须非空**：`size == 0`、只有一行 `# XXX` 的 README、或全是占位文件 → 视同无开源。
- **必须是论文自有**：只引用、只使用第三方工具（vLLM / TRL / crawlee…）不算。
- 承诺「录用后开源」不算。

详见 `references/opensource-gate.md`。

### 2c. 会议门（硬门槛）

- **有录用声明**（`Accepted to/at`、`to appear in`、`Published in`、`Proceedings of`）→ 用 `references/venues.yaml` 的 `name` + `aliases` 做匹配。
  - 命中 → 放行，`venue_score` 取该会议分数。
  - **未命中 → 淘汰**（不在放行名单的会议一律不放行）。
  - 无法核验的会议 → 按未命中处理，报告注明。
- **无录用声明**（纯 preprint）→ 放行，`venue_score = preprint_score`（默认 0.5）。

别名匹配要能处理全称/简称/旧称（NIPS ↔ NeurIPS）。详见 `references/venue-gate.md` §2。

## 3. 相关度打分（relevance 0–10）

只对过门论文打分，由模型判断，**不做多维评分**。

| 分档 | 含义 |
|---|---|
| 9–10 | 正中 `direction.name`，问题与方法都对得上 |
| 7–8 | 明确属于该方向，但非最核心 |
| 5–6 | 边缘相关，需借道才用得上 |
| 3–4 | 仅关键词表面命中 |
| 0–2 | 不相关 |

`relevance < min_relevance`（默认 5）→ 不交付，记 `low_relevance` 入 seen.csv。

## 4. 交付

精简卡片，推送到当前会话。模板与示例见 `references/digest-format.md`。

日报头部必含：日期、方向名、`arXiv 命中 X → 会议补 Y → 交付 N/max`。

篇数 **1–5**。若窗口内确实 0 篇合格：**如实报告 0 篇并说明卡在哪道门**，不凑数、不放水。0 篇是正常结果，不是故障。

## 5. 台账

两份 CSV，schema 见 `references/state-files.md`。

| 文件 | 语义 |
|---|---|
| `data/pushed.csv` | 已发送。**永久**，命中即不再发送 |
| `data/seen.csv` | 已评估但被拒。**30 天滚动复核**：<30 天直接跳过；≥30 天重新评估并覆盖该行 |

三层去重键：arXiv ID / DOI / 归一化标题（NFKC → 小写 → 去标点 → 压空格）。

**同日重跑**：若本次全部命中 pushed.csv 导致 0 篇，明确提示「今日已推送过，N 篇已在台账中」，不要静默返回空。

**台账纪律**：只追加或按规则覆盖，禁止凭记忆重写。重写前必须逐行对照 `data/daily/` 的历史日报核对。

## 6. 抓取纪律

- **curl 优先**：所有网络抓取先 `curl -sS --noproxy '*'`。失败（非 200 / 超时 / 连接错误）才降级。
- **websearch 降级不限次数**，但必须在报告里列台账（第几次、查什么、为什么降级）。
- **标题保真（硬性）**：标题只从 arXiv abs 页 `<h1>` 或 API 返回逐字符取，**禁止改写、简写、翻译、去副标题**。历史上标题被改写会直接导致查不到。
- arXiv API 限速：请求间隔 ≥3s；429 指数退避；**406 是端点级封锁，不是限流**，立即降级 websearch（`arxiv.org/search/?query=…&order=-announced_date_first`）。
- 单批 `max_results ≤ 30`，多组分抓。**不要用 80**——会退化成返回旧结果。

## 7. 规则对照

| 用户规则 | 落点 |
|---|---|
| 只看 A 会 + 额外放行的 B 会 | `references/venues.yaml` 唯一真源，会议门 2c |
| 会议分数一致、允许修改 | `default_score` + 单会 `score` 覆盖 |
| 存关注方向，按方向搜 | `references/profile.yaml`，启动检查 §0 |
| arXiv 优先，没有则搜会议，≤5 篇 | 主流程 ①⑦ |
| curl 优先，其次 websearch | §6 |
| 录用会议不在名单的不放行 | 2c |
| 无开源代码一律不放行 | 2b |
| 存已发送论文，不二次发送 | §5 pushed.csv |

## References

| 文件 | 用途 |
|---|---|
| `references/profile.yaml` | 研究画像：方向、关键词、类目、窗口、篇数 |
| `references/venues.yaml` | 放行会议名单与分数（唯一真源） |
| `references/venue-gate.md` | 综述/benchmark 判据 + 会议别名匹配 + preprint 处理 |
| `references/opensource-gate.md` | 开源门槛：查什么、算不算、怎么判空 |
| `references/arxiv-channel.md` | arXiv 检索式构造、限速、降级、标题保真 |
| `references/conference-channel.md` | 会议兜底：curl 试探 → websearch → 回查 arXiv |
| `references/digest-format.md` | 精简卡片模板与示例 |
| `references/state-files.md` | 两份 CSV 的 schema 与 30 天滚动逻辑 |
| `scripts/arxiv_search.py` | 抓取 + Atom 解析 + 去重 |
| `scripts/dedup.py` | 台账读写 |
