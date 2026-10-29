---
name: CS-literature-pipeline
description: |
  Search recent papers for a user-defined research direction: arXiv first, then user-approved venues
  (conferences or journals), with open-source and venue gates and a permanent delivery ledger.
  Use when the user asks 找论文 / 搜文献 / 今日文献 / 追踪某方向的新论文 / 有没有新论文.
license: MIT
metadata:
  version: 1.0.0
  tags: [literature, arxiv, search, dedup, conference, whitelist]
---

# Literature Pipeline

按用户指定的研究方向检索论文。会议和期刊统一为 venue；用户可以仅提供名称，不强制类型、CCF 等级或别名。期刊使用官方在线发表日期窗口，并须核验发表状态。

**本 skill 只定义「怎么搜」，不定义「何时搜」。** 调度方式（cron、手动、被其他 agent 调用）由用户在 skill 之外决定，本文档不涉及。

## 0. 启动检查（每次运行第一步）

读 `references/profile.yaml` 与 `references/venues.yaml`，逐项确认：

| 项 | 字段 | 缺失时 |
|---|---|---|
| 关注方向 | `direction.keywords` | 访谈补齐 |
| arXiv 类目 | `arxiv_categories` | 按方向提议 → 用户确认 |
| 会议年份窗 | `conference_years` | 扫描会议前访谈补齐；仅期刊场景可不填 |
| 放行发表场所 | `venues` | 访谈补齐，会议与期刊均可 |

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
   按 relevance 降序、venue_score 降序、发表时间由新到旧排序。
   venue_score 只在相关度相同时作为次排序键；会议和期刊不按类型区别对待。

④ 截断交付
   按上述顺序取前 max_papers（默认 5）篇；relevance < min_relevance 的一律不交付

⑤ 交付
   精简卡片 → 当前会话（格式见 references/digest-format.md）

⑥ 归档
   pushed.csv 追加（永久去重）· data/daily/YYYY-MM-DD.md 存当天日报

⑦ 兜底：arXiv 交付不足 max_papers → 按 venues.yaml 顺序逐个扫描 venue（会议或期刊）
   官方 proceedings / 期刊目录等 → 关键词预筛 → 核验论文与发表状态
   → 同一套门槛 → 补到 max_papers 或已验证完整的名单耗尽
   记录断点、来源质量和不完整状态，禁止将部分名单或失败视为扫完。
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

### 2c. Venue 门（硬门槛）

- **有发表或录用声明**（会议或期刊）→ 用 `references/venues.yaml` 的 `name` + `aliases` 做匹配。
  - 命中 → 放行，`venue_score` 取该 venue 分数。
  - **未命中 → 淘汰**（不在放行名单的 venue 一律不放行）。
  - 无法核验的 venue → 按未命中处理，报告注明。
- 同时检查 Comments、Journal Reference 和 DOI；有发表线索时先核验，不因 Comments 未提到发表就当纯 preprint。
- **无录用或发表声明**（纯 preprint）→ 放行，`venue_score = preprint_score`（默认 0.5）。
- 期刊兜底须核验正式发表或官方在线发表；投稿或未经核验的接收声明不算。

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

日报头部必含：日期、方向名、`arXiv 命中 X → venue 兜底补 Y → 交付 N/max`。

篇数 **1–5**。若窗口内确实 0 篇合格：**如实报告 0 篇并说明卡在哪道门**，不凑数、不放水。0 篇是正常结果，不是故障。

## 5. 台账

两份 CSV，schema 见 `references/state-files.md`。

| 文件 | 语义 |
|---|---|
| `data/pushed.csv` | 已发送。**永久**，命中即不再发送 |
| `data/seen.csv` | 已评估但被拒。**30 天滚动复核**：<30 天直接跳过；≥30 天重新评估并覆盖该行；`venue_not_allowed` 的名单指纹变化时立即重评 |

三层去重键：arXiv ID / DOI / 归一化标题（NFKC → 小写 → 去标点 → 压空格）。

**同日重跑**：若本次全部命中 pushed.csv 导致 0 篇，明确提示「今日已推送过，N 篇已在台账中」，不要静默返回空。

**台账纪律**：只追加或按规则覆盖，禁止凭记忆重写。重写前必须逐行对照 `data/daily/` 的历史日报核对。

## 6. 抓取纪律

- **curl 优先**：所有网络抓取先 `curl -sS --noproxy '*'`。失败（非 200 / 超时 / 连接错误）才降级。
- **websearch 降级不限次数**，但必须在报告里列台账（第几次、查什么、为什么降级）。
- **标题保真（硬性）**：标题只从 arXiv abs 页 `<h1>` 或 API 返回逐字符取，**禁止改写、简写、翻译、去副标题**。历史上标题被改写会直接导致查不到。
- arXiv API 限速：请求间隔 ≥3s；429 指数退避；**406 是端点级封锁，不是限流**，立即降级 websearch（`arxiv.org/search/?query=…&order=-announced_date_first`）。
- 单批 `max_results ≤ 30`，按页抓取；默认总预算 90，可通过 `--max` 调整。达到预算但无法证明扫完时必须报告可能截断；页失败不能报告完整。**不要单批用 80**——会退化成返回旧结果。

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
| `references/venues.yaml` | 放行的会议和期刊名单与分数（唯一真源） |
| `references/venue-gate.md` | 综述/benchmark 判据 + 会议别名匹配 + preprint 处理 |
| `references/opensource-gate.md` | 开源门槛：查什么、算不算、怎么判空 |
| `references/arxiv-channel.md` | arXiv 检索式构造、限速、降级、标题保真 |
| `references/conference-channel.md` | 会议兜底：curl 试探 → websearch → 回查 arXiv |
| `references/digest-format.md` | 精简卡片模板与示例 |
| `references/state-files.md` | 两份 CSV 的 schema 与 30 天滚动逻辑 |
| `scripts/arxiv_search.py` | 抓取 + Atom 解析 + 去重 |
| `scripts/dedup.py` | 台账读写 |
| `scripts/doctor.py` | 离线体检及可选联网探针 |
| `scripts/venue_state.py` | venue 扫描检查点（agent 显式调用） |
| `scripts/acl_anthology.py` | ACL Anthology 卷和论文链接提取 |
| `scripts/journal_search.py` | Crossref 期刊候选，需官方核验 |
