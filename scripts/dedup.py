#!/usr/bin/env python3
"""台账读写：pushed.csv（永久）与 seen.csv（30 天滚动复核）。

用法：
    # 查某篇论文是否可交付
    python3 scripts/dedup.py check --title "..." [--arxiv-id 2610.11111] [--doi 10.x/y]

    # 记为已推送
    python3 scripts/dedup.py push --title "..." --venue NeurIPS --year 2026 --score 0.90 \
        [--arxiv-id 2610.11111] [--doi 10.x/y]

    # 记为已淘汰（会覆盖 seen.csv 里同篇的旧行）
    python3 scripts/dedup.py reject --title "..." --reason no_code [--arxiv-id ...]

    # 打印台账摘要
    python3 scripts/dedup.py stats

退出码：
    check  → 0 可交付 / 1 已推送（跳过） / 2 30 天内已评估（跳过） / 3 已过复核期（应重评）
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA = SKILL_ROOT / "data"
PUSHED = DATA / "pushed.csv"
SEEN = DATA / "seen.csv"

PUSHED_COLS = [
    "arxiv_id", "doi", "title_norm", "title",
    "venue", "venue_year", "pushed_at", "score",
]
SEEN_COLS = [
    "arxiv_id", "doi", "title_norm", "title",
    "reason", "first_seen", "last_checked",
]
REVIEW_DAYS = 30


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
    with path.open(encoding="utf-8", newline="") as f:
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


def cmd_check(args) -> int:
    keys = keys_of(args.arxiv_id, args.doi, args.title)
    pushed = read_rows(PUSHED)
    if find(pushed, keys) >= 0:
        print(json.dumps({"status": "pushed", "action": "skip"}, ensure_ascii=False))
        return 1

    seen = read_rows(SEEN)
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


def cmd_push(args) -> int:
    ensure(PUSHED, PUSHED_COLS)
    keys = keys_of(args.arxiv_id, args.doi, args.title)
    rows = read_rows(PUSHED)

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
    write_rows(PUSHED, PUSHED_COLS, rows)

    # 若曾进过 seen.csv，交付后移除
    if SEEN.exists():
        seen = read_rows(SEEN)
        j = find(seen, keys)
        if j >= 0:
            seen.pop(j)
            write_rows(SEEN, SEEN_COLS, seen)

    print(json.dumps({"status": "pushed", "total": len(rows)}, ensure_ascii=False))
    return 0


def cmd_reject(args) -> int:
    ensure(SEEN, SEEN_COLS)
    keys = keys_of(args.arxiv_id, args.doi, args.title)
    rows = read_rows(SEEN)
    today = date.today().isoformat()

    i = find(rows, keys)
    if i >= 0:
        # 覆盖：保留 first_seen，更新 reason / last_checked
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
        })
        action = "appended"

    write_rows(SEEN, SEEN_COLS, rows)
    print(json.dumps({"status": action, "total": len(rows)}, ensure_ascii=False))
    return 0


def cmd_stats(args) -> int:
    pushed = read_rows(PUSHED)
    seen = read_rows(SEEN)

    by_reason: dict[str, int] = {}
    for r in seen:
        by_reason[r.get("reason", "?")] = by_reason.get(r.get("reason", "?"), 0) + 1

    print(json.dumps({
        "pushed_total": len(pushed),
        "seen_total": len(seen),
        "seen_by_reason": by_reason,
        "pushed_dates": sorted({r.get("pushed_at", "") for r in pushed if r.get("pushed_at")})[-5:],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="台账读写")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--title", required=True)
        p.add_argument("--arxiv-id", default="")
        p.add_argument("--doi", default="")

    p1 = sub.add_parser("check", help="查是否可交付")
    common(p1)
    p1.set_defaults(func=cmd_check)

    p2 = sub.add_parser("push", help="记为已推送")
    common(p2)
    p2.add_argument("--venue", default="preprint")
    p2.add_argument("--year", default="")
    p2.add_argument("--score", default="")
    p2.set_defaults(func=cmd_push)

    p3 = sub.add_parser("reject", help="记为已淘汰")
    common(p3)
    p3.add_argument("--reason", required=True,
                    choices=["survey_benchmark", "no_code", "venue_not_allowed", "low_relevance"])
    p3.set_defaults(func=cmd_reject)

    p4 = sub.add_parser("stats", help="台账摘要")
    p4.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
