#!/usr/bin/env python3
"""arXiv 检索 + Atom 解析 + 台账去重。

用法：
    python3 scripts/arxiv_search.py --days 7 --max 30
    python3 scripts/arxiv_search.py --days 7 --max 30 --out /tmp/c.json --json

从 references/profile.yaml 读关键词与类目，从 data/ 读台账做去重。
输出候选列表（已去重），供后续门槛评估。

退出码：
    0  正常
    2  配置缺失（profile.yaml 未填方向/类目）
    3  网络失败（已尝试重试与退避）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
PROFILE = SKILL_ROOT / "references" / "profile.yaml"
DATA = SKILL_ROOT / "data"
PUSHED = DATA / "pushed.csv"
SEEN = DATA / "seen.csv"

API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARX = "{http://arxiv.org/schemas/atom}"

USER_AGENT = "literature-pipeline/1.0 (research; contact: local)"


# ---------------------------------------------------------------- 配置


def load_profile() -> dict:
    """读 profile.yaml。缺 pyyaml 时用极简解析兜底（只支持本文件的扁平结构）。"""
    if not PROFILE.exists():
        return {}
    text = PROFILE.read_text(encoding="utf-8")
    try:
        import yaml

        return yaml.safe_load(text) or {}
    except ImportError:
        return _mini_yaml(text)


def _mini_yaml(text: str) -> dict:
    """极简 YAML 兜底：只解析 key: value 与 key: [a, b]。"""
    out: dict = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].rstrip()
        if not line or line.startswith(" ") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            out[k] = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
        elif v:
            out[k] = v.strip("\"'")
    return out


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "")
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- 台账


def load_ledger(path: Path) -> dict[str, dict]:
    """读 CSV 台账，返回 {key: row}，key 覆盖 arxiv_id / doi / title_norm。"""
    idx: dict[str, dict] = {}
    if not path.exists():
        return idx
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            for field in ("arxiv_id", "doi", "title_norm"):
                val = (row.get(field) or "").strip()
                if val:
                    idx[val.lower()] = row
    return idx


def in_pushed(paper: dict, idx: dict[str, dict]) -> bool:
    for key in (
        (paper.get("arxiv_id") or "").lower(),
        (paper.get("doi") or "").lower(),
        norm_title(paper.get("title", "")),
    ):
        if key and key in idx:
            return True
    return False


def seen_recent(paper: dict, idx: dict[str, dict], days: int = 30) -> dict | None:
    """命中 seen.csv 且未到复核期 → 返回该行（调用方据此跳过）。"""
    for key in (
        (paper.get("arxiv_id") or "").lower(),
        (paper.get("doi") or "").lower(),
        norm_title(paper.get("title", "")),
    ):
        if key and key in idx:
            row = idx[key]
            last = (row.get("last_checked") or "").strip()
            if not last:
                return row
            try:
                checked = datetime.strptime(last, "%Y-%m-%d").date()
            except ValueError:
                return row
            if (datetime.now().date() - checked).days < days:
                return row
            return None  # 已过期，允许重评
    return None


# ---------------------------------------------------------------- 检索式


def build_query(profile: dict) -> str:
    keywords = profile.get("keywords") or profile.get("direction", {}).get("keywords") or []
    cats = profile.get("arxiv_categories") or []
    if isinstance(profile.get("direction"), dict):
        keywords = keywords or profile["direction"].get("keywords") or []

    if not keywords:
        return ""

    # 关键词内部用 OR 连接（含短语），多组之间用 AND
    terms = []
    for kw in keywords:
        kw = str(kw).strip()
        if not kw:
            continue
        if " " in kw:
            terms.append(f'"{kw}"')
        else:
            terms.append(f"all:{kw}")
    kw_expr = " OR ".join(terms)

    cat_expr = ""
    if cats:
        cat_expr = "(" + " OR ".join(f"cat:{c}" for c in cats) + ")"

    if cat_expr:
        return f"({kw_expr}) AND {cat_expr}"
    return kw_expr


# ---------------------------------------------------------------- 抓取


def fetch(query: str, max_results: int, timeout: int = 30, retries: int = 3) -> str:
    params = {
        "search_query": query,
        "start": 0,
        "max_results": min(max_results, 30),  # 硬上限 30，80 会退化成旧结果
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = API + "?" + urllib.parse.urlencode(params)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 等价 --noproxy '*'

    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with opener.open(req, timeout=timeout) as resp:
                code = resp.getcode()
                if code == 200:
                    return resp.read().decode("utf-8", "replace")
                last_err = f"HTTP {code}"
        except urllib.error.HTTPError as e:
            if e.code == 406:
                # 端点级封锁，不是限流 —— 不重试
                raise RuntimeError("HTTP 406: arXiv 端点封锁，请降级 websearch") from e
            if e.code == 429:
                wait = 10 * (2**attempt)
                last_err = f"HTTP 429, backoff {wait}s"
                time.sleep(wait)
                continue
            last_err = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"

        time.sleep(3)  # 请求间隔 ≥3s

    raise RuntimeError(f"arXiv 抓取失败：{last_err}")


def parse(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall(f"{ATOM}entry"):
        def txt(tag: str) -> str:
            el = entry.find(f"{ATOM}{tag}")
            return (el.text or "").strip() if el is not None else ""

        raw_id = txt("id")  # http://arxiv.org/abs/2610.12345v1
        m = re.search(r"/abs/([^/]+?)(v\d+)?$", raw_id)
        arxiv_id = m.group(1) if m else raw_id

        cats = [c.get("term") for c in entry.findall(f"{ATOM}category") if c.get("term")]
        authors = [
            (a.findtext(f"{ATOM}name") or "").strip()
            for a in entry.findall(f"{ATOM}author")
        ]

        doi_el = entry.find(f"{ARX}doi")
        comment_el = entry.find(f"{ARX}comment")

        out.append(
            {
                "arxiv_id": arxiv_id,
                "title": txt("title").replace("\n", " ").strip(),  # 逐字符保留，仅去换行
                "abstract": re.sub(r"\s+", " ", txt("summary")).strip(),
                "authors": [a for a in authors if a],
                "published": txt("published"),
                "updated": txt("updated"),
                "categories": cats,
                "doi": (doi_el.text or "").strip() if doi_el is not None else "",
                "comments": (comment_el.text or "").strip() if comment_el is not None else "",
                "abs_url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
            }
        )
    return out


def in_window(paper: dict, days: int) -> bool:
    stamp = paper.get("published") or ""
    try:
        dt = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(days=days)


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description="arXiv 检索 + 去重")
    ap.add_argument("--days", type=int, default=None, help="时间窗（天），默认读 profile")
    ap.add_argument("--max", type=int, default=30, help="单批条数（≤30）")
    ap.add_argument("--out", default="", help="输出 JSON 路径")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    ap.add_argument("--no-dedup", action="store_true", help="跳过去重（调试用）")
    args = ap.parse_args()

    profile = load_profile()
    days = args.days or int(profile.get("window_days") or 7)

    query = build_query(profile)
    if not query:
        print(
            "配置缺失：references/profile.yaml 未填写 direction.keywords。\n"
            "请先完成配置访谈（见 profile.yaml 头部注释），再运行。",
            file=sys.stderr,
        )
        return 2

    try:
        xml_text = fetch(query, args.max)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 3

    papers = [p for p in parse(xml_text) if in_window(p, days)]

    if not args.no_dedup:
        pushed = load_ledger(PUSHED)
        seen = load_ledger(SEEN)
        kept, skipped = [], {"pushed": 0, "seen": 0}
        for p in papers:
            if in_pushed(p, pushed):
                skipped["pushed"] += 1
                continue
            if seen_recent(p, seen) is not None:
                skipped["seen"] += 1
                continue
            kept.append(p)
        papers = kept
        print(
            f"去重：跳过已推送 {skipped['pushed']} 篇、30 天内已评估 {skipped['seen']} 篇",
            file=sys.stderr,
        )

    payload = {"query": query, "days": days, "count": len(papers), "papers": papers}

    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已写入 {args.out}（{len(papers)} 篇）", file=sys.stderr)

    if args.json or not args.out:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
