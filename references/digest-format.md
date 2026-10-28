# Digest Format — 精简卡片

交付到当前会话。**精简**，不做精读展开。

---

## 模板

```markdown
📅 {YYYY-MM-DD} 文献检索 | {direction.name}
arXiv 命中 {A} → 会议补 {B} → 交付 {N}/{max}（窗口 {window_days} 天）

━━━━━━━━━━━━━━━━━━━━

#{i} {Title}
📍 {Venue} {Year} | 分数 {venue_score} × 相关度 {relevance}/10 = {final} | {preprint 或 已录用}
💡 {一句话：这篇解决了什么问题、做了什么、结果如何}
🔗 代码：{code_url}
📄 arXiv：{abs_url}
🧭 方向：{direction 标签}

━━━━━━━━━━━━━━━━━━━━
```

## 字段规则

| 字段 | 规则 |
|---|---|
| Title | **逐字符**取自 arXiv，禁止改写（见 `arxiv-channel.md` §5） |
| Venue | 已录用写会议名 + 年份；preprint 写 `preprint` |
| 分数 | 明示 `venue_score × relevance/10 = final`，便于核对 |
| 一句话 | 15 秒能判断值不值得点开。写「做了什么 + 关键结果」，不写「提出了一种新方法」这种空话 |
| 代码链接 | 开源门验证过的那个链接，不是随便找的 |
| arXiv 链接 | `https://arxiv.org/abs/<id>` |
| 方向 | 论文自身的方向标签，帮助定位归属 |

**不要**加：阅读深度、方法论长篇、关键结果罗列、点评段落、vault 关联。这些属于精读，不在本 skill 范围。

---

## 头部统计（必含）

```
arXiv 命中 {A} → 会议补 {B} → 交付 {N}/{max}（窗口 {window_days} 天）
淘汰：综述/benchmark {x} · 无开源 {y} · 会议不在名单 {z} · 相关度不足 {w}
```

被「会议不在名单」淘汰的，**列出会议名**，方便用户决定要不要加进 `venues.yaml`。

---

## 特殊情况

**0 篇**：

```
📅 {YYYY-MM-DD} 文献检索 | {direction.name}
本次 0 篇交付。

arXiv 近 {window_days} 天候选 {A} 篇 → 过门 0 篇
淘汰分布：综述/benchmark {x} · 无开源 {y} · 会议不在名单 {z} · 相关度不足 {w}
会议渠道：已扫 {m}/{total} 会，未补足。

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
arXiv 命中 34 → 会议补 2 → 交付 3/5（窗口 7 天）
淘汰：综述/benchmark 3 · 无开源 11 · 会议不在名单 4 · 相关度不足 9

━━━━━━━━━━━━━━━━━━━━

#1 Learning User Preference Shifts via Temporal Memory Retrieval
📍 NeurIPS 2026 | 分数 1.0 × 相关度 9/10 = 0.90 | 已录用
💡 用时间衰减的记忆检索跟踪用户偏好漂移，在 3 个推荐基准上把长期满意度提升 12%，且无需重训。
🔗 代码：https://github.com/example/temporal-mem
📄 arXiv：https://arxiv.org/abs/2610.11111
🧭 方向：Personalized LLM

━━━━━━━━━━━━━━━━━━━━

#2 ...
```
