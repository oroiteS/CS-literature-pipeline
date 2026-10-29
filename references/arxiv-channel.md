# arXiv Channel — 主渠道

arXiv 永远是第一渠道。只有它交付不足 `max_papers` 时，才按用户配置顺序启用 venue 兜底（会议或期刊）。

---

## §1 检索式构造

关键词来自 `profile.yaml` 的 `direction.keywords`，**必须覆盖同义变体**，不能只用单一说法。

```
检索式 = (kw1 OR "kw phrase" OR variant1) AND (kw2 OR variant2) ...
类目限制 = cat:cs.CL OR cat:cs.AI ...
时间 = submittedDate 区间 [今天 - window_days, 今天]
```

**同义变体是硬要求**。历史教训：只查 `"language model"` 会漏掉用缩写 `LLM` 的论文。

建议变体构造法：
- 缩写 ↔ 全称：`LLM` ↔ `language model`
- 词形变化：`personalization` / `personalized` / `personalizing`
- 短语加引号：`"user model*"` / `"user profil*"`
- 领域惯用词：`preference learning` / `user adaptation`

**类目**来自 `profile.yaml` 的 `arxiv_categories`，由用户在配置访谈中确认。**不要假设用户是计算机方向**——经济学用 `econ.EM`，量化用 `q-fin.*`，生物用 `q-bio.*`。

---

## §2 调用方式

```bash
curl -sS --noproxy '*' --max-time 30 \
  "https://export.arxiv.org/api/query?search_query=<encoded>&start=0&max_results=30&sortBy=submittedDate&sortOrder=descending"
```

`--noproxy '*'` 是必需的：arXiv 走代理会失败。

或直接用脚本（推荐，内含解析与去重）：

```bash
python3 scripts/arxiv_search.py --days 7 --max 90 --out /tmp/candidates.json
```

---

## §3 限速与降级

| 现象 | 处理 |
|---|---|
| 正常 | 请求间隔 **≥3 秒** |
| HTTP 429 "Rate exceeded" | 指数退避，首次等 10s，最多重试 3 次 |
| **HTTP 406 空响应** | **端点级封锁，不是限流**。改 UA / Accept / HTTP 版本都无效，**立即降级 websearch** |
| 连接超时 | 重试 1 次，仍失败 → 降级 websearch |

降级后走：

```bash
curl -sS --noproxy '*' --max-time 60 \
  "https://arxiv.org/search/?query=<terms>&searchtype=all&order=-announced_date_first&size=50"
```

注意：**HTML 搜索不支持布尔语法**。空格 = AND。需要 OR 时拆成多次简单查询，再用 `Submitted` 日期过滤窗口。

---

## §4 分页纪律

- **单批 `max_results ≤ 30`**。
- **绝不用 80**。历史实测：`max_results=80` 配合按日期排序会**退化成返回旧结果**，看起来正常但全是陈年论文。
- `--max` 为总抓取预算，默认 90；每页最多 30，按 `start` 分页并限速。达到预算但不能证明完整时标记可能截断；页失败标记失败，不能当作完整。
- 时间窗之前的结果可用于停止分页；网页搜索降级结果须明确标明其完整性未知。

---

## §5 标题保真（硬性规则）

标题只从以下两处**逐字符**取：
1. arXiv API 返回的 `<title>` 元素
2. abs 页的 `<h1 class="title">`

**禁止**：改写、简写、翻译、去副标题、补全缩写、修正大小写。

历史上因标题被改写，导致按标题回查会议论文时全部查不到。这条没有例外。

---

## §6 候选输出

每篇候选至少带：

```json
{
  "arxiv_id": "2610.12345",
  "title": "原始标题（逐字符）",
  "abstract": "...",
  "authors": ["..."],
  "published": "2026-10-25T03:32:01Z",
  "updated": "2026-10-25T03:32:01Z",
  "categories": ["cs.CL"],
  "comments": "Accepted to XXX 2026. Code: https://...",
  "doi": "10.example/identifier",
  "journal_ref": "官方发表信息（如有）",
  "abs_url": "https://arxiv.org/abs/2610.12345",
  "pdf_url": "https://arxiv.org/pdf/2610.12345"
}
```

`comments` 字段是关键——开源门和会议门都靠它，必须在抓取时就带上。
