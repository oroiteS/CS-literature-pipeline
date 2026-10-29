# Venue Gate — 综述/benchmark 门 + 发表场所门

两件事都在本文件：**先杀综述/benchmark**，**再核会议或期刊**。顺序不可颠倒（综述判定只看标题摘要，成本最低）。
会议和期刊使用同一用户名单，不按类型区别打分。期刊兜底必须核验正式发表或官方在线发表，不能把仅投稿、未核验的接收声明当成已发表。纯 preprint 保持原有放行规则。

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

1. 同时检查 arXiv 的 **Comments、Journal Reference 和 DOI**；Comments 非空并不意味着没有期刊发表记录。
2. 对存在 DOI 或 journal reference 的论文核对官方论文页或可信元数据，确认发表场所及状态；来源不一致或无法核验时明确标记，不得直接当作纯 preprint。
3. 有需要时抓 HTML / PDF 前几页检查 `Accepted to|Accepted at|to appear in|Published in|Proceedings of` 等声明。

### 2.2 判定

| 情形 | 处理 |
|---|---|
| 有声明，命中 `venues.yaml` 的 name/aliases | **放行**，`venue_score` = 该场所分数 |
| 有声明，未命中 | **淘汰**（不在放行名单的会议或期刊一律不放行） |
| 有声明，但 venue 无法核验 | 按未命中处理，报告注明「venue 无法核验」 |
| **无任何录用声明（纯 preprint）** | **放行**，`venue_score = preprint_score`（默认 0.5） |

### 2.3 别名匹配

大小写不敏感，NFKC 归一化并将标点转换为空格，再匹配完整词组；缩写的前后须为非字母数字或文本边界，不能裸做子串匹配。别名须由用户在名单中提供，不能凭空推断。

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

`venue_score` 仅在相关度相同时作次排序键，不再与相关度相乘；preprint 仍放行。

---

## §3 报告要求

每次运行必须能回答：
- 有多少篇因综述/benchmark 被淘汰
- 有多少篇因「会议不在名单」被淘汰（列出会议名，供用户决定是否加名单）
- 有多少篇因「无开源」被淘汰
- 有多少篇是 preprint 放行

这些数字进日报头部，不省略。
