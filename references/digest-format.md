# Digest Format — 精简卡片

交付到当前会话。**精简**，不做精读展开。

---

## 模板

```markdown
📅 {YYYY-MM-DD} 文献检索 | {direction.name}
arXiv 命中 {A} → venue 兜底补 {B} → 交付 {N}/{max}（窗口 {window_days} 天）

━━━━━━━━━━━━━━━━━━━━

#{i} {Title}
📍 {Venue} {Year} | 相关度 {relevance}/10 · venue 分数 {venue_score} | {发表状态}
💡 {一句话：这篇解决了什么问题、做了什么、结果如何}
🔗 代码：{code_url}
📄 arXiv：{abs_url}
🧭 方向：{direction 标签}

━━━━━━━━━━━━━━━━━━━━
```

## 字段规则

| 字段 | 规则 |
|---|---|
| Title | **逐字符**取自 arXiv 或经核验的官方发表元数据，禁止改写（见 `arxiv-channel.md` §5） |
| Venue | 会议或期刊名 + 年份；preprint 写 `preprint` |
| 分数 | 明示相关度及 venue 分数；先按相关度排序，同分再比较 venue 分数 |
| 一句话 | 15 秒能判断值不值得点开。写「做了什么 + 关键结果」，不写「提出了一种新方法」这种空话 |
| 代码链接 | 开源门验证过的那个链接，不是随便找的 |
| 论文链接 | 有 arXiv ID 时给出 arXiv；否则给出经核验的 DOI 或官方论文页面 |
| 方向 | 论文自身的方向标签，帮助定位归属 |

**不要**加：阅读深度、方法论长篇、关键结果罗列、点评段落、vault 关联。这些属于精读，不在本 skill 范围。

---

## 头部统计（必含）

```
arXiv 命中 {A} → venue 兜底补 {B} → 交付 {N}/{max}（窗口 {window_days} 天）
淘汰：综述/benchmark {x} · 无开源 {y} · venue 不在名单 {z} · 相关度不足 {w}
```

被「venue 不在名单」淘汰的，**列出场所名称**，方便用户决定要不要加进 `venues.yaml`。

---

## 特殊情况

**0 篇**：

```
📅 {YYYY-MM-DD} 文献检索 | {direction.name}
本次 0 篇交付。

arXiv 近 {window_days} 天候选 {A} 篇 → 过门 0 篇
淘汰分布：综述/benchmark {x} · 无开源 {y} · venue 不在名单 {z} · 相关度不足 {w}
venue 渠道：已扫 {m}/{total}，未补足；列出未扫、部分与失败的来源。

原因：{一句话说明卡在哪}
```

0 篇是正常结果，不是故障。**如实报告，不凑数、不放水**。

**同日重跑 0 篇**：

```
📅 {YYYY-MM-DD} 文献检索 | {direction.name}
今日已推送过，{N} 篇已在台账中（pushed.csv），本次无新增。
```

**配置缺失**：

```
⚠️ 无法运行：{缺失项} 未配置。
需要你提供：{具体要什么、什么格式}
```

---

## 示例（数据为虚构）

```markdown
📅 2026-10-27 文献检索 | 个性化大模型
arXiv 命中 34 → venue 兜底补 2 → 交付 3/5（窗口 7 天）
淘汰：综述/benchmark 3 · 无开源 11 · venue 不在名单 4 · 相关度不足 9

━━━━━━━━━━━━━━━━━━━━

#1 Learning User Preference Shifts via Temporal Memory Retrieval
📍 NeurIPS 2026 | 相关度 9/10 · venue 分数 1.0 | 已录用
💡 用时间衰减的记忆检索跟踪用户偏好漂移，在 3 个推荐基准上把长期满意度提升 12%，且无需重训。
🔗 代码：https://github.com/example/temporal-mem
📄 arXiv：https://arxiv.org/abs/2610.11111
🧭 方向：Personalized LLM

━━━━━━━━━━━━━━━━━━━━

#2 ...
```
