#!/usr/bin/env python3
"""台账与待推队列读写：pushed.csv（永久）、queue.json（无上限优先级队列）、seen.csv（30 天滚动复核）。

用法：
    # 查某篇论文是否可评估/交付
    python3 scripts/dedup.py check --title "..." [--arxiv-id 2610.11111] [--doi 10.x/y]

    # 合格论文入队（保存完整卡片元数据，队列无硬上限）
    python3 scripts/dedup.py enqueue --title "..." --relevance 9 --venue NeurIPS --year 2026 \
        --venue-score 1.0 --published 2026-10-25 --pub-status 已录用 \
        --summary "..." --code-url "https://..." --paper-url "https://..." --direction-tag "..." \
        [--arxiv-id 2610.11111] [--doi 10.x/y]
    python3 scripts/dedup.py enqueue --json-in /tmp/qualified.json

    # 按优先级弹出今日配额（默认 max_papers - 今日已推）并写入 pushed.csv
    python3 scripts/dedup.py pop [--limit N] [--dry-run]

    # 查看待推队列水位、今日配额与 >30 积压告警状态
    python3 scripts/dedup.py queue-status
    python3 scripts/dedup.py queue-list

    # 直接记为已推送（兼容旧调用；若在队列中会同步移除）
    python3 scripts/dedup.py push --title "..." --venue NeurIPS --year 2026 --score 0.90 \
        [--arxiv-id 2610.11111] [--doi 10.x/y]

    # 记为已淘汰（会覆盖 seen.csv 里同篇的旧行）
    python3 scripts/dedup.py reject --title "..." --reason no_code [--arxiv-id ...]

    # 打印台账与队列摘要
    python3 scripts/dedup.py stats

退出码：
    check  → 0 可评估 / 1 已推送（跳过） / 2 30 天内已评估（跳过） / 3 已过复核期（应重评） / 4 已在待推队列（跳过）
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from datetime import date, datetime
from pathlib import Path

try:
    from .config import ConfigError, load_yaml
    from .doctor import resolve_data_dir
    from .venue_review import venue_fingerprint, venue_recheck_required
except ImportError:  # 直接执行 scripts/dedup.py
    from config import ConfigError, load_yaml
    from doctor import resolve_data_dir
    from venue_review import venue_fingerprint, venue_recheck_required

SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA = SKILL_ROOT / "data"
PUSHED = DATA / "pushed.csv"
SEEN = DATA / "seen.csv"
QUEUE = DATA / "queue.json"
PROFILE = SKILL_ROOT / "references" / "profile.yaml"
_DEFAULT_PUSHED, _DEFAULT_SEEN, _DEFAULT_QUEUE = PUSHED, SEEN, QUEUE

DEFAULT_MAX_PAPERS = 5
DEFAULT_QUEUE_WARN_THRESHOLD = 30
QUEUE_SCHEMA_VERSION = 1

PUSHED_COLS = [
    "arxiv_id", "doi", "title_norm", "title",
    "venue", "venue_year", "pushed_at", "score",
]
SEEN_COLS = [
    "arxiv_id", "doi", "title_norm", "title",
    "reason", "first_seen", "last_checked", "venue_fingerprint",
]
REVIEW_DAYS = 30


def _load_profile_cfg() -> dict:
    return load_yaml(PROFILE)


def ledger_paths() -> tuple[Path, Path]:
    """每次操作读取画像，保留测试对单个台账常量的显式覆盖。"""
    pushed_path, seen_path, _ = all_ledger_paths()
    return pushed_path, seen_path


def all_ledger_paths() -> tuple[Path, Path, Path]:
    """返回 (pushed.csv, seen.csv, queue.json) 路径，保留测试覆写。"""
    data = resolve_data_dir(_load_profile_cfg(), SKILL_ROOT)
    return (
        PUSHED if PUSHED != _DEFAULT_PUSHED else data / "pushed.csv",
        SEEN if SEEN != _DEFAULT_SEEN else data / "seen.csv",
        QUEUE if QUEUE != _DEFAULT_QUEUE else data / "queue.json",
    )


def queue_settings() -> tuple[int, int]:
    """返回 (daily_limit, warn_threshold)。"""
    profile = _load_profile_cfg()
    max_papers = profile.get("max_papers", DEFAULT_MAX_PAPERS)
    warn_threshold = profile.get("queue_warn_threshold", DEFAULT_QUEUE_WARN_THRESHOLD)
    if type(max_papers) is not int or max_papers <= 0:
        raise ConfigError("max_papers 必须是正整数")
    if type(warn_threshold) is not int or warn_threshold <= 0:
        raise ConfigError("queue_warn_threshold 必须是正整数")
    return max_papers, warn_threshold


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "")
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def ensure(path: Path, cols: list[str]) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=cols).writeheader()


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, cols: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    tmp.replace(path)  # 原子替换，避免写坏


def read_queue(path: Path) -> list[dict]:
    """读取待推队列；文件不存在时视为空队列，格式损坏时报错。"""
    if not path.exists():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"queue.json 读取失败：{exc}") from exc
    if (
        not isinstance(obj, dict)
        or obj.get("schema_version") != QUEUE_SCHEMA_VERSION
        or not isinstance(obj.get("items"), list)
    ):
        raise ValueError("queue.json 格式无效：需要 schema_version=1 与 items 列表")
    items = []
    for idx, item in enumerate(obj["items"]):
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip():
            raise ValueError(f"queue.json 第 {idx} 项缺少有效 title")
        items.append(item)
    return items


def write_queue(path: Path, items: list[dict]) -> None:
    """原子落盘待推队列。"""
    payload = json.dumps(
        {"schema_version": QUEUE_SCHEMA_VERSION, "items": items},
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".queue-", delete=False
        ) as f:
            name = f.name
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        name = None
    finally:
        if name is not None:
            os.unlink(name)


def keys_of(arxiv_id: str, doi: str, title: str) -> list[str]:
    ks = []
    if arxiv_id:
        ks.append(arxiv_id.strip().lower())
    if doi:
        ks.append(doi.strip().lower())
    nt = norm_title(title)
    if nt:
        ks.append(nt)
    return ks


def find(rows: list[dict], keys: list[str]) -> int:
    for i, r in enumerate(rows):
        for field in ("arxiv_id", "doi", "title_norm"):
            v = (r.get(field) or "").strip().lower()
            if v and v in keys:
                return i
    return -1


def _finite_number(value, label: str, *, low: float | None = None, high: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是有限数字")
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是有限数字") from exc
    if not math.isfinite(num):
        raise ValueError(f"{label} 必须是有限数字")
    if low is not None and num < low:
        raise ValueError(f"{label} 不能小于 {low}")
    if high is not None and num > high:
        raise ValueError(f"{label} 不能大于 {high}")
    return num


def _published_sort_value(item: dict) -> str:
    """返回可按字典序比较的新旧时间键（YYYY-MM-DD 或 ISO 时间戳，越新越大）。"""
    pub = str(item.get("published") or "").strip()
    if pub:
        m = re.match(r"^(\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}Z?)?)", pub)
        if m:
            return m.group(1)
    year = str(item.get("venue_year") or "").strip()
    if re.fullmatch(r"\d{4}", year):
        return f"{year}-01-01"
    return ""


def _queued_sort_value(item: dict) -> str:
    """先入先出兜底键；缺失日期排在后面。"""
    q = str(item.get("queued_at") or "").strip()
    return q if re.fullmatch(r"\d{4}-\d{2}-\d{2}", q) else "9999-99-99"


def sort_queue_items(items: list[dict]) -> list[dict]:
    """优先级排序：relevance 降序 → venue_score 降序 → 发表时间新到旧 → queued_at 先入先出。"""
    indexed = list(enumerate(items))
    # Python sort 稳定：先按最次要键排，再按主排序键排
    indexed.sort(key=lambda pair: (_queued_sort_value(pair[1]), pair[0]))
    indexed.sort(key=lambda pair: _published_sort_value(pair[1]), reverse=True)
    indexed.sort(
        key=lambda pair: (
            _finite_number(pair[1].get("relevance", 0), "relevance"),
            _finite_number(pair[1].get("venue_score", 0), "venue_score"),
        ),
        reverse=True,
    )
    return [item for _, item in indexed]


def _normalize_queue_item(raw: dict, today: str) -> dict:
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("入队项缺少非空 title")
    relevance = _finite_number(raw.get("relevance"), "relevance", low=0, high=10)
    venue_score = _finite_number(raw.get("venue_score", 0.5), "venue_score", low=0)
    arxiv_id = str(raw.get("arxiv_id") or "").strip()
    doi = str(raw.get("doi") or "").strip()
    paper_url = str(raw.get("paper_url") or raw.get("abs_url") or "").strip()
    if not paper_url and arxiv_id:
        paper_url = f"https://arxiv.org/abs/{arxiv_id}"
    queued_at = str(raw.get("queued_at") or today).strip() or today
    return {
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title_norm": norm_title(title),
        "title": title,
        "venue": str(raw.get("venue") or "preprint").strip() or "preprint",
        "venue_year": str(raw.get("venue_year") or raw.get("year") or "").strip(),
        "relevance": relevance,
        "venue_score": venue_score,
        "published": str(raw.get("published") or "").strip(),
        "pub_status": str(raw.get("pub_status") or "").strip(),
        "summary": str(raw.get("summary") or "").strip(),
        "code_url": str(raw.get("code_url") or "").strip(),
        "paper_url": paper_url,
        "direction_tag": str(raw.get("direction_tag") or "").strip(),
        "source": str(raw.get("source") or "arxiv").strip() or "arxiv",
        "queued_at": queued_at,
    }


def compute_queue_summary(
    queue_items: list[dict],
    pushed_rows: list[dict],
    *,
    daily_limit: int,
    warn_threshold: int,
    today: date | None = None,
) -> dict:
    today_date = today or date.today()
    today_str = today_date.isoformat()
    today_pushed = sum(1 for r in pushed_rows if (r.get("pushed_at") or "").strip() == today_str)
    remaining_quota = max(0, daily_limit - today_pushed)

    by_tier = {"high_9_10": 0, "mid_7_8": 0, "edge_5_6": 0, "other": 0}
    oldest_date: date | None = None
    for item in queue_items:
        rel = _finite_number(item.get("relevance", 0), "relevance")
        if rel >= 9:
            by_tier["high_9_10"] += 1
        elif rel >= 7:
            by_tier["mid_7_8"] += 1
        elif rel >= 5:
            by_tier["edge_5_6"] += 1
        else:
            by_tier["other"] += 1
        q = str(item.get("queued_at") or "").strip()
        if q:
            try:
                qd = datetime.strptime(q, "%Y-%m-%d").date()
            except ValueError:
                qd = None
            if qd is not None and (oldest_date is None or qd < oldest_date):
                oldest_date = qd

    total = len(queue_items)
    return {
        "queue_total": total,
        "warn_threshold": warn_threshold,
        "warn_exceeded": total > warn_threshold,
        "by_tier": by_tier,
        "oldest_queued_at": oldest_date.isoformat() if oldest_date else None,
        "oldest_days": max(0, (today_date - oldest_date).days) if oldest_date else 0,
        "daily_limit": daily_limit,
        "today_pushed": today_pushed,
        "remaining_quota": remaining_quota,
    }


def _format_score_for_pushed(item: dict) -> str:
    rel = item.get("relevance", "")
    vscore = item.get("venue_score", "")
    if rel != "" and vscore != "":
        return f"rel={rel:g};venue={vscore:g}" if isinstance(rel, (int, float)) and isinstance(vscore, (int, float)) else f"rel={rel};venue={vscore}"
    return str(rel if rel != "" else vscore)


def cmd_check(args) -> int:
    pushed_path, seen_path, queue_path = all_ledger_paths()
    keys = keys_of(args.arxiv_id, args.doi, args.title)
    pushed = read_rows(pushed_path)
    if find(pushed, keys) >= 0:
        print(json.dumps({"status": "pushed", "action": "skip"}, ensure_ascii=False))
        return 1

    queued = read_queue(queue_path)
    if find(queued, keys) >= 0:
        print(json.dumps({"status": "queued", "action": "skip"}, ensure_ascii=False))
        return 4

    seen = read_rows(seen_path)
    i = find(seen, keys)
    if i < 0:
        print(json.dumps({"status": "new", "action": "evaluate"}, ensure_ascii=False))
        return 0

    row = seen[i]
    last = (row.get("last_checked") or "").strip()
    if last:
        try:
            days = (date.today() - datetime.strptime(last, "%Y-%m-%d").date()).days
        except ValueError:
            days = REVIEW_DAYS
    else:
        days = REVIEW_DAYS

    if venue_recheck_required(row):
        print(json.dumps(
            {"status": "seen_venue_changed", "action": "re-evaluate", "reason": row.get("reason", ""), "days_since": days},
            ensure_ascii=False,
        ))
        return 3

    if days < REVIEW_DAYS:
        print(json.dumps(
            {"status": "seen", "action": "skip", "reason": row.get("reason", ""), "days_since": days},
            ensure_ascii=False,
        ))
        return 2

    print(json.dumps(
        {"status": "seen_expired", "action": "re-evaluate", "reason": row.get("reason", ""), "days_since": days},
        ensure_ascii=False,
    ))
    return 3


def cmd_enqueue(args) -> int:
    pushed_path, seen_path, queue_path = all_ledger_paths()
    daily_limit, warn_threshold = queue_settings()
    today = date.today().isoformat()

    if args.json_in:
        if args.json_in == "-":
            raw_payload = json.load(sys.stdin)
        else:
            raw_payload = json.loads(Path(args.json_in).read_text(encoding="utf-8"))
        if isinstance(raw_payload, dict) and isinstance(raw_payload.get("items"), list):
            raw_items = raw_payload["items"]
        elif isinstance(raw_payload, list):
            raw_items = raw_payload
        elif isinstance(raw_payload, dict):
            raw_items = [raw_payload]
        else:
            raise ValueError("--json-in 必须是 JSON 对象或对象数组")
        batch_mode = True
    else:
        if not args.title or args.relevance is None:
            raise ValueError("单篇入队必须提供 --title 和 --relevance（或使用 --json-in）")
        raw_items = [{
            "arxiv_id": args.arxiv_id,
            "doi": args.doi,
            "title": args.title,
            "venue": args.venue,
            "venue_year": args.year,
            "relevance": args.relevance,
            "venue_score": args.venue_score,
            "published": args.published,
            "pub_status": args.pub_status,
            "summary": args.summary,
            "code_url": args.code_url,
            "paper_url": args.paper_url,
            "direction_tag": args.direction_tag,
            "source": args.source,
        }]
        batch_mode = False

    pushed = read_rows(pushed_path)
    queued = read_queue(queue_path)
    seen = read_rows(seen_path) if seen_path.exists() else []
    seen_dirty = False
    queue_dirty = False

    enqueued, skipped_pushed, skipped_queued = 0, 0, 0
    for raw in raw_items:
        item = _normalize_queue_item(raw, today)
        keys = keys_of(item["arxiv_id"], item["doi"], item["title"])
        if find(pushed, keys) >= 0:
            skipped_pushed += 1
            continue
        if find(queued, keys) >= 0:
            skipped_queued += 1
            continue
        queued.append(item)
        queue_dirty = True
        enqueued += 1
        j = find(seen, keys)
        if j >= 0:
            seen.pop(j)
            seen_dirty = True

    if queue_dirty:
        write_queue(queue_path, queued)
    if seen_dirty:
        write_rows(seen_path, SEEN_COLS, seen)

    summary = compute_queue_summary(
        queued, pushed, daily_limit=daily_limit, warn_threshold=warn_threshold
    )
    if not batch_mode:
        if skipped_pushed:
            print(json.dumps({"status": "already_pushed", "action": "skip", **summary}, ensure_ascii=False))
            return 1
        if skipped_queued:
            print(json.dumps({"status": "already_queued", "action": "skip", **summary}, ensure_ascii=False))
            return 1
        print(json.dumps({"status": "enqueued", **summary}, ensure_ascii=False))
        return 0

    print(json.dumps({
        "status": "batch_enqueued",
        "enqueued": enqueued,
        "skipped_pushed": skipped_pushed,
        "skipped_queued": skipped_queued,
        **summary,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_pop(args) -> int:
    pushed_path, seen_path, queue_path = all_ledger_paths()
    daily_limit, warn_threshold = queue_settings()
    pushed = read_rows(pushed_path)
    queued = read_queue(queue_path)
    today = date.today().isoformat()

    before_summary = compute_queue_summary(
        queued, pushed, daily_limit=daily_limit, warn_threshold=warn_threshold
    )
    if args.limit is not None:
        if args.limit < 0:
            raise ValueError("--limit 不能为负数")
        limit = args.limit
    else:
        limit = before_summary["remaining_quota"]

    # 过滤掉任何已出现在 pushed.csv 中的陈旧队列项，保证绝不重复推送
    eligible = []
    stale_removed = 0
    for item in queued:
        keys = keys_of(item.get("arxiv_id", ""), item.get("doi", ""), item.get("title", ""))
        if find(pushed, keys) >= 0:
            stale_removed += 1
            continue
        eligible.append(item)

    ordered = sort_queue_items(eligible)
    selected = ordered[:limit]
    remaining_items = ordered[limit:]

    if not args.dry_run:
        if selected:
            ensure(pushed_path, PUSHED_COLS)
            seen = read_rows(seen_path) if seen_path.exists() else []
            seen_dirty = False
            for item in selected:
                pushed.append({
                    "arxiv_id": item.get("arxiv_id", ""),
                    "doi": item.get("doi", ""),
                    "title_norm": item.get("title_norm") or norm_title(item.get("title", "")),
                    "title": item.get("title", ""),
                    "venue": item.get("venue") or "preprint",
                    "venue_year": item.get("venue_year", ""),
                    "pushed_at": today,
                    "score": _format_score_for_pushed(item),
                })
                keys = keys_of(item.get("arxiv_id", ""), item.get("doi", ""), item.get("title", ""))
                j = find(seen, keys)
                if j >= 0:
                    seen.pop(j)
                    seen_dirty = True
            # 先写 pushed.csv，再更新 queue.json：即使中途崩溃也绝不重复发送
            write_rows(pushed_path, PUSHED_COLS, pushed)
            if seen_dirty:
                write_rows(seen_path, SEEN_COLS, seen)
        if selected or stale_removed:
            write_queue(queue_path, remaining_items)

    after_pushed = pushed if not args.dry_run else pushed + [
        {"pushed_at": today} for _ in selected
    ]
    after_summary = compute_queue_summary(
        remaining_items, after_pushed, daily_limit=daily_limit, warn_threshold=warn_threshold
    )
    print(json.dumps({
        "dry_run": bool(args.dry_run),
        "delivered_count": len(selected),
        "delivered": selected,
        "stale_removed": stale_removed,
        "queue_before": len(eligible),
        **after_summary,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_queue_status(args) -> int:
    pushed_path, _, queue_path = all_ledger_paths()
    daily_limit, warn_threshold = queue_settings()
    pushed = read_rows(pushed_path)
    queued = read_queue(queue_path)
    summary = compute_queue_summary(
        queued, pushed, daily_limit=daily_limit, warn_threshold=warn_threshold
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_queue_list(args) -> int:
    pushed_path, _, queue_path = all_ledger_paths()
    daily_limit, warn_threshold = queue_settings()
    pushed = read_rows(pushed_path)
    queued = sort_queue_items(read_queue(queue_path))
    summary = compute_queue_summary(
        queued, pushed, daily_limit=daily_limit, warn_threshold=warn_threshold
    )
    print(json.dumps({**summary, "items": queued}, ensure_ascii=False, indent=2))
    return 0


def cmd_push(args) -> int:
    pushed_path, seen_path, queue_path = all_ledger_paths()
    ensure(pushed_path, PUSHED_COLS)
    keys = keys_of(args.arxiv_id, args.doi, args.title)
    rows = read_rows(pushed_path)

    if find(rows, keys) >= 0:
        print(json.dumps({"status": "already_pushed", "action": "skip"}, ensure_ascii=False))
        return 1

    rows.append({
        "arxiv_id": args.arxiv_id or "",
        "doi": args.doi or "",
        "title_norm": norm_title(args.title),
        "title": args.title,
        "venue": args.venue or "preprint",
        "venue_year": args.year or "",
        "pushed_at": date.today().isoformat(),
        "score": args.score or "",
    })
    write_rows(pushed_path, PUSHED_COLS, rows)

    # 若曾进过 seen.csv 或 queue.json，交付后同步移除
    if seen_path.exists():
        seen = read_rows(seen_path)
        j = find(seen, keys)
        if j >= 0:
            seen.pop(j)
            write_rows(seen_path, SEEN_COLS, seen)

    if queue_path.exists():
        queued = read_queue(queue_path)
        q_idx = find(queued, keys)
        if q_idx >= 0:
            queued.pop(q_idx)
            write_queue(queue_path, queued)

    print(json.dumps({"status": "pushed", "total": len(rows)}, ensure_ascii=False))
    return 0


def cmd_reject(args) -> int:
    _, seen_path, _ = all_ledger_paths()
    ensure(seen_path, SEEN_COLS)
    keys = keys_of(args.arxiv_id, args.doi, args.title)
    rows = read_rows(seen_path)
    today = date.today().isoformat()

    fingerprint = venue_fingerprint() if args.reason == "venue_not_allowed" else ""
    i = find(rows, keys)
    if i >= 0:
        # 覆盖：保留 first_seen，更新 reason / last_checked
        rows[i]["venue_fingerprint"] = fingerprint
        rows[i]["reason"] = args.reason
        rows[i]["last_checked"] = today
        if args.arxiv_id:
            rows[i]["arxiv_id"] = args.arxiv_id
        if args.doi:
            rows[i]["doi"] = args.doi
        rows[i]["title"] = args.title
        rows[i]["title_norm"] = norm_title(args.title)
        action = "updated"
    else:
        rows.append({
            "arxiv_id": args.arxiv_id or "",
            "doi": args.doi or "",
            "title_norm": norm_title(args.title),
            "title": args.title,
            "reason": args.reason,
            "first_seen": today,
            "last_checked": today,
            "venue_fingerprint": fingerprint,
        })
        action = "appended"

    write_rows(seen_path, SEEN_COLS, rows)
    print(json.dumps({"status": action, "total": len(rows)}, ensure_ascii=False))
    return 0


def cmd_stats(args) -> int:
    pushed_path, seen_path, queue_path = all_ledger_paths()
    daily_limit, warn_threshold = queue_settings()
    pushed = read_rows(pushed_path)
    seen = read_rows(seen_path)
    queued = read_queue(queue_path)

    by_reason: dict[str, int] = {}
    for r in seen:
        by_reason[r.get("reason", "?")] = by_reason.get(r.get("reason", "?"), 0) + 1

    queue_summary = compute_queue_summary(
        queued, pushed, daily_limit=daily_limit, warn_threshold=warn_threshold
    )
    print(json.dumps({
        "pushed_total": len(pushed),
        "seen_total": len(seen),
        "seen_by_reason": by_reason,
        "pushed_dates": sorted({r.get("pushed_at", "") for r in pushed if r.get("pushed_at")})[-5:],
        **queue_summary,
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="台账与待推队列读写")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, *, title_required: bool = True):
        p.add_argument("--title", required=title_required, default="")
        p.add_argument("--arxiv-id", default="")
        p.add_argument("--doi", default="")

    p1 = sub.add_parser("check", help="查是否可评估/交付")
    common(p1)
    p1.set_defaults(func=cmd_check)

    p2 = sub.add_parser("push", help="记为已推送")
    common(p2)
    p2.add_argument("--venue", default="preprint")
    p2.add_argument("--year", "--venue-year", dest="year", default="")
    p2.add_argument("--score", default="")
    p2.set_defaults(func=cmd_push)

    p3 = sub.add_parser("reject", help="记为已淘汰")
    common(p3)
    p3.add_argument("--reason", required=True,
                    choices=["survey_benchmark", "no_code", "venue_not_allowed", "low_relevance"])
    p3.set_defaults(func=cmd_reject)

    p4 = sub.add_parser("stats", help="台账与队列摘要")
    p4.set_defaults(func=cmd_stats)

    p5 = sub.add_parser("enqueue", help="合格论文加入待推队列")
    common(p5, title_required=False)
    p5.add_argument("--venue", default="preprint")
    p5.add_argument("--year", "--venue-year", dest="year", default="")
    p5.add_argument("--relevance", type=float, default=None)
    p5.add_argument("--venue-score", type=float, default=0.5)
    p5.add_argument("--published", default="")
    p5.add_argument("--pub-status", default="")
    p5.add_argument("--summary", default="")
    p5.add_argument("--code-url", default="")
    p5.add_argument("--paper-url", default="")
    p5.add_argument("--direction-tag", default="")
    p5.add_argument("--source", default="arxiv")
    p5.add_argument("--json-in", default="", help="批量入队 JSON 文件路径（- 表示 stdin）")
    p5.set_defaults(func=cmd_enqueue)

    p6 = sub.add_parser("pop", help="按优先级弹出今日配额并记入 pushed.csv")
    p6.add_argument("--limit", type=int, default=None, help="覆盖弹出上限（默认取今日剩余配额）")
    p6.add_argument("--dry-run", action="store_true", help="仅预览出队结果，不落盘")
    p6.set_defaults(func=cmd_pop)

    p7 = sub.add_parser("queue-status", help="查看待推队列水位与积压告警状态")
    p7.set_defaults(func=cmd_queue_status)

    p8 = sub.add_parser("queue-list", help="按优先级列出待推队列全部论文")
    p8.set_defaults(func=cmd_queue_list)

    args = ap.parse_args()
    try:
        return args.func(args)
    except (ConfigError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
