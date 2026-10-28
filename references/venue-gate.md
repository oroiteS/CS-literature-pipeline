# Venue Gate — 综述/benchmark 门 + 会议门

两件事都在本文件：**先杀综述/benchmark**，**再核录用会议**。顺序不可颠倒（综述判定只看标题摘要，成本最低）。

---

## §1 综述 / benchmark 门

主贡献是以下任一类 → **直接淘汰**，不进入打分。

| 类型 | 判据 |
|---|---|
| 综述 / Survey / Review | 标题含 Survey / Review / Overview，或摘要以 "we survey / we review / this paper reviews" 为主旨 |
| Position / Opinion | 以观点主张为主贡献，无新方法无新实验 |
| Benchmark / 评测集 | 摘要以 "we introduce/propose/present … benchmark/evaluation suite/testbed" 为中心贡献，主产物是排行榜或评测框架 |
| Dataset 发布 | 主贡献是数据集本身（不是方法） |

**不算淘汰的情况**：方法论文顺带发了个数据集、或在自己的实验里用了 benchmark。判据看**主贡献**，不看是否出现这些词。

---

## §2 会议门

### 2.1 取录用声明

按成本从低到高：

1. arXiv abs 页的 **Comments 字段**（最常见，如 `Accepted to CIKM 2026`）
2. abs 缺失时，抓 HTML / PDF 前几页 grep：
   `Accepted to|Accepted at|to appear in|Published in|Proceedings of|© \d{4} (IEEE|ACM)`

### 2.2 判定

| 情形 | 处理 |
|---|---|
| 有声明，命中 `venues.yaml` 的 name/aliases | **放行**，`venue_score` = 该会分数 |
| 有声明，未命中 | **淘汰**（不在放行名单的会议一律不放行） |
| 有声明，但会议无法核验 | 按未命中处理，报告注明「venue 无法核验」 |
| **无任何录用声明（纯 preprint）** | **放行**，`venue_score = preprint_score`（默认 0.5） |

### 2.3 别名匹配

大小写不敏感、去标点后做包含匹配。

```
normalize(s) = NFKC → lower → 去非字母数字 → 压空格
命中条件：normalize(声明文本) 包含 normalize(name) 或任一 normalize(alias)
```

必须能处理：
- 简称 ↔ 全称：`NIPS` ↔ `NeurIPS` ↔ `Neural Information Processing Systems`
- 带年份：`Accepted to NeurIPS 2026` → 命中 `NeurIPS`
- 带 track：`EMNLP 2026 Findings` → 命中 `EMNLP`（是否收 Findings 由用户在名单 note 里说明；名单没写就按命中处理并如实标注 track）

**警惕误命中**：`ACL` 会命中 `ACL Anthology`、`EACL`、`NAACL` 里的子串。匹配时要求词边界（前后是非字母或字符串边界），不要裸 `in` 判断。

### 2.4 会议 vs preprint 的分数

```
有录用且命中名单  → venue_score = 该会 score（默认 1.0）
无录用声明        → venue_score = preprint_score（默认 0.5）
```

preprint 折半的理由：未过评审，可信度低于已录用论文；但仍放行，否则 arXiv 渠道几乎每天 0 篇。

---

## §3 报告要求

每次运行必须能回答：
- 有多少篇因综述/benchmark 被淘汰
- 有多少篇因「会议不在名单」被淘汰（列出会议名，供用户决定是否加名单）
- 有多少篇因「无开源」被淘汰
- 有多少篇是 preprint 放行

这些数字进日报头部，不省略。
