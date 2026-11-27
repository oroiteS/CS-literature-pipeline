# State Files — 台账与待推队列 schema

三份状态文件默认在 `data/` 下；若 profile 的 `data_dir` 指定其他目录，检索、体检和台账脚本统一使用该目录。用 `scripts/dedup.py` 读写，不要手写。

---

## §1 `data/pushed.csv` — 已发送（永久）

```csv
arxiv_id,doi,title_norm,title,venue,venue_year,pushed_at,score
2610.11111,,learning user preference shifts via temporal memory retrieval,Learning User Preference Shifts via Temporal Memory Retrieval,NeurIPS,2026,2026-10-27,0.90
```

| 列 | 说明 |
|---|---|
| `arxiv_id` | arXiv ID，无则空 |
| `doi` | DOI，无则空 |
| `title_norm` | 归一化标题（去重主键） |
| `title` | 原始标题（人读） |
| `venue` | 会议名 / `preprint` |
| `venue_year` | 会议年份 |
| `pushed_at` | 推送日期 `YYYY-MM-DD` |
| `score` | 最终分数 |

**语义：永久**。命中即不再发送，无过期。

---

## §1.5 `data/queue.json` — 待推优先级队列（无硬上限）

所有通过三道门槛且 `relevance >= min_relevance` 的合格论文先写入此队列，保存完整卡片字段；每次运行按优先级最多弹出当日剩余配额（默认每日 5 篇）推送给用户，未推完的留在队列中次日续推，避免重复抓取与重复消耗 token。

```json
{
  "schema_version": 1,
  "items": [
    {
      "arxiv_id": "2610.11111",
      "doi": "",
      "title_norm": "learning user preference shifts via temporal memory retrieval",
      "title": "Learning User Preference Shifts via Temporal Memory Retrieval",
      "venue": "NeurIPS",
      "venue_year": "2026",
      "relevance": 9.0,
      "venue_score": 1.0,
      "published": "2026-10-25T03:32:01Z",
      "pub_status": "已录用",
      "summary": "一句话总结",
      "code_url": "https://github.com/example/temporal-mem",
      "paper_url": "https://arxiv.org/abs/2610.11111",
      "direction_tag": "Personalized LLM",
      "source": "arxiv",
      "queued_at": "2026-10-27"
    }
  ]
}
```

### 队列规则

- **容量**：不设硬上限。
- **积压汇报**：当交付后队列剩余篇数 `> queue_warn_threshold`（默认 `30`）时，`queue-status` / `pop` 返回 `warn_exceeded: true` 及分数段分布（`high_9_10` / `mid_7_8` / `edge_5_6` / `other`）和 `oldest_queued_at` / `oldest_days`，直接向用户汇报，由用户自行决定后续安排。
- **优先级排序**：`relevance` 降序 → `venue_score` 降序 → 发表时间（`published` 或 `venue_year`）由新到旧 → `queued_at` 先入先出。
- **出队原子顺序**：`dedup.py pop` 先追加写入 `pushed.csv`，再原子更新 `queue.json` 移除已弹出项；即使中途崩溃也不会重发。
- **去重优先级**：`pushed.csv`（跳过）→ `queue.json`（已在队，跳过不重评）→ `seen.csv`（30 天内跳过）。合格论文入队若曾出现在 `seen.csv`（如 30 天复核或名单变动后通过），自动从 `seen.csv` 移除。

### 常用命令

```bash
python3 scripts/dedup.py queue-status                 # 查今日剩余配额、队列水位与告警状态
python3 scripts/dedup.py enqueue --json-in /tmp/q.json # 批量（或单篇参数）写入合格论文
python3 scripts/dedup.py pop [--dry-run]              # 按优先级弹出今日配额并记入 pushed.csv
python3 scripts/dedup.py queue-list                   # 按优先级查看当前队列全部论文
```

---

## §2 `data/seen.csv` — 已评估但被拒（30 天滚动）

```csv
arxiv_id,doi,title_norm,title,reason,first_seen,last_checked,venue_fingerprint
2610.22222,,some paper title,Some Paper Title,no_code,2026-10-01,2026-10-27,
```

| 列 | 说明 |
|---|---|
| `reason` | `survey_benchmark` / `no_code` / `venue_not_allowed` / `low_relevance` |
| `first_seen` | 首次评估日期 |
| `last_checked` | 最近一次评估日期 |
| `venue_fingerprint` | `venue_not_allowed` 判定时的名单内容指纹；旧行缺失时保守重评 |

仅 `venue_not_allowed` 在名单指纹变化时立即重评；其他原因保持原有 30 天规则。旧 CSV 无该列仍可读取，后续写入时补列。

### 30 天滚动复核逻辑

```
今天遇到论文 P：
  P 在 pushed.csv  → 跳过（永久）
  P 在 queue.json  → 跳过（已在待推队列，不重评）
  P 在 seen.csv：
      today - last_checked < 30 天  → 跳过（不重评，省 token）
      today - last_checked >= 30 天 → 重新评估
                                        仍拒 → 覆盖该行（更新 reason / last_checked）
                                        通过 → 从 seen.csv 删除，入队 queue.json
  P 都不在 → 正常评估，通过则入队 queue.json，被拒则追加 seen.csv
```

**为什么滚动**：论文可能在 30 天内开源了、中了会了，永久拉黑会漏掉。30 天是复核周期，不是惩罚期。

**覆盖而非追加**：同一篇论文在 seen.csv 里只保留一行，避免文件膨胀。

---

## §3 去重键

三层，命中任一即视为同一篇：

1. **arXiv ID** — 有则最强，直接比对
2. **DOI** — 有则比对
3. **title_norm** — 归一化标题

### 标题归一化

```python
def norm_title(t):
    t = unicodedata.normalize("NFKC", t)
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)   # 去标点
    t = re.sub(r"\s+", " ", t).strip()
    return t
```

注意：不要去掉数字（`GPT-4` 与 `GPT-3` 是不同论文）。

---

## §4 纪律

- **只追加或按规则覆盖**，禁止凭记忆重写整个文件。
- 重写前必须逐行对照 `data/daily/` 下的历史日报核对——记忆里的 ID 可能是编造的。
- 每次运行结束必须回写；中途崩溃则下次从现有台账继续，不会重发已推论文。

---

## §5 `data/daily/YYYY-MM-DD.md`

当天日报的完整副本（即推送给用户的内容）。用途：事后复查、台账核对。

同一天多次运行 → 追加到同一文件，用分隔线隔开，并标注第几次运行。
