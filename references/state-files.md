# State Files — 台账 schema

两份 CSV 默认在 `data/` 下；若 profile 的 `data_dir` 指定其他目录，检索、体检和台账脚本统一使用该目录。用 `scripts/dedup.py` 读写，不要手写。

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
  P 在 seen.csv：
      today - last_checked < 30 天  → 跳过（不重评，省 token）
      today - last_checked >= 30 天 → 重新评估
                                        仍拒 → 覆盖该行（更新 reason / last_checked）
                                        通过 → 从 seen.csv 删除，正常交付
  P 都不在 → 正常评估，被拒则追加
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
