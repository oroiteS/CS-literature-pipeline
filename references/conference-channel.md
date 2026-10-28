# Conference Channel — 兜底渠道

**唯一触发条件**：arXiv 渠道交付不足 `max_papers`。交付已满则不启动。

---

## §1 流程

```
逐个放行会议（按 venues.yaml 顺序）
  │
  ├─ ① 尝试 curl 标准 URL 模式（免费，成本近零）
  │     OpenReview / PMLR / ACL Anthology / 会议官网
  │     200 → 解析名单
  │     失败 → 降级 websearch
  │
  ├─ ② websearch 取 accepted papers
  │     查询式：「<会议名> <年份> accepted papers」
  │     结果可能是博客汇总、部分列表 → 能拿多少拿多少，如实标注
  │
  ├─ ③ 本地关键词预筛（零网络）
  │     标题（+可得摘要）匹配 profile.yaml 的 keywords 与 exclude
  │     命中 → 候选
  │
  ├─ ④ 回查 arXiv 拿 ID / 摘要 / Comments
  │     按标题批量查：ti:"…" OR ti:"…"，每批 5–8 条，间隔 ≥10s
  │     查不到 → 第一作者 + 关键词单查
  │     仍查不到 → 记「名单遗珠」，不深挖
  │
  ├─ ⑤ 进同一套门槛（综述/benchmark → 开源 → 会议）
  │     会议门证据 = 名单成员身份（官方实锤），abs Comments 作交叉验证
  │
  └─ ⑥ 补到 max_papers 或名单耗尽
```

---

## §2 curl 标准 URL 模式（先试，免费）

| 渠道 | URL 模式 | 格式 |
|---|---|---|
| OpenReview | `https://api2.openreview.net/notes/search?query=<q>` | JSON |
| PMLR | `https://proceedings.mlr.press/v<NNN>/` | 静态 HTML |
| ACL Anthology | `https://aclanthology.org/volumes/<YYYY>.<venue>-main/` | 静态 HTML |
| 会议官网 | 各会 accepted papers 页 | HTML |

**注意**：
- ACL Anthology 的**事件总页极大（~19MB）**，必须用单卷 URL 并加 `--max-time 90`。
- Anthology 站内搜索是 Google CSE（要密钥）→ **不可用**，只用卷宗页。
- ACM DL 有反爬 → 直接走 websearch `site:dl.acm.org`。
- 部分 AAAI / KDD 历史届只发 PDF → **不进池**（机器不可读）。
- DBLP 在部分网络连不通 → 不作主通道。

---

## §3 websearch 降级

- **次数不限**，但每次必须记台账：第几次、查什么、为什么降级。
- 查询式建议：`"<会议名> <年份> accepted papers"`、`site:openreview.net "<会议名> <年份>"`
- 拿到的名单质量如实标注：`完整名单` / `部分列表` / `仅博客汇总`

---

## §4 年份窗

按 `profile.yaml` 的 `conference_years` 过滤（如 `[2025, 2026]`）。**由用户指定，bot 不自行默认**。

会议论文没有「今天」概念，年份窗是它唯一的时效约束。

---

## §5 停止条件

满足任一即停：

1. 交付满 `max_papers` → 立即停
2. 名单全部耗尽 → 停，如实报差额（**不降标准凑数**）
3. 用户预设的调用预算耗尽 → 停，报告剩余未扫会议

---

## §6 台账必报

每次运行报告：

- 扫了几个会 / 名单共几个
- 名单总数 → 关键词预筛 → 去重挡下 → arXiv 可查 → 门槛幸存 → 交付
- 「名单有但 arXiv 查不到」单独计数（本渠道固有损耗）
- 哪些会 curl 失败降级了 websearch、哪些完全拿不到名单

**禁止**「假装扫完」：没扫的会必须列出来。
