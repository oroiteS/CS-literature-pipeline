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
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from .config import ConfigError, load_config, parse_yaml
    from .venue_review import venue_fingerprint, venue_recheck_required
    from .doctor import main as doctor_main, resolve_data_dir
except ImportError:
    from config import ConfigError, load_config, parse_yaml
    from venue_review import venue_fingerprint, venue_recheck_required
    from doctor import main as doctor_main, resolve_data_dir

SKILL_ROOT = Path(__file__).resolve().parent.parent
PROFILE = SKILL_ROOT / "references" / "profile.yaml"
VENUES = SKILL_ROOT / "references" / "venues.yaml"
DATA = SKILL_ROOT / "data"
PUSHED = DATA / "pushed.csv"
SEEN = DATA / "seen.csv"
_DEFAULT_PUSHED, _DEFAULT_SEEN = PUSHED, SEEN

API = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARX = "{http://arxiv.org/schemas/atom}"

USER_AGENT = "CS-literature-pipeline/1.0 (research; contact: local)"


# ---------------------------------------------------------------- 配置


def load_profile() -> dict:
    """读取并校验完整运行配置，保证配置缺失时不发起网络请求。"""
    profile, _ = load_config(PROFILE, VENUES)
    return profile


def _mini_yaml(text: str) -> dict:
    """兼容原接口；不支持的 YAML 语法明确报错。"""
    return parse_yaml(text)


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
    with path.open(encoding="utf-8-sig", newline="") as f:
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


def seen_recent(paper: dict, idx: dict[str, dict], days: int = 30,
                fingerprint: str | None = None) -> dict | None:
    """命中 seen.csv 且未到复核期 → 返回该行（调用方据此跳过）。"""
    for key in (
        (paper.get("arxiv_id") or "").lower(),
        (paper.get("doi") or "").lower(),
        norm_title(paper.get("title", "")),
    ):
        if key and key in idx:
            row = idx[key]
            if venue_recheck_required(row, fingerprint):
                return None
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


_last_request = None


def fetch(query: str, max_results: int, timeout: int = 30, retries: int = 3, *, start: int = 0) -> str:
    """抓取单页；所有页面及重试之间均至少间隔三秒。"""
    global _last_request
    if not 1 <= max_results <= 30 or start < 0:
        raise ValueError("单页条数必须为 1–30，start 必须非负")
    params = {
        "search_query": query,
        "start": start,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = API + "?" + urllib.parse.urlencode(params)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 等价 --noproxy '*'

    last_err = None
    for attempt in range(retries):
        if _last_request is not None:
            delay = 3 - (time.monotonic() - _last_request)
            if delay > 0:
                time.sleep(delay)
        _last_request = time.monotonic()
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
                if attempt + 1 < retries:
                    time.sleep(wait)
                continue
            last_err = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"

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
        journal_el = entry.find(f"{ARX}journal_ref")

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
                "journal_ref": (journal_el.text or "").strip() if journal_el is not None else "",
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


def search_pages(query: str, days: int, budget: int) -> tuple[list[dict], dict]:
    """按 submittedDate 降序分页，报告完整性；失败时保留已成功解析的页。"""
    if budget < 1 or days < 1:
        raise ValueError("days 和总预算必须为正整数")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    report = {"complete": False, "truncated": False, "failed": False,
              "reason": "", "fetched": 0, "pages": 0, "total_results": None,
              "budget": budget}
    papers = []
    previous = None
    observed_ids = set()
    start = 0
    opensearch = "{http://a9.com/-/spec/opensearch/1.1/}"
    try:
        while start < budget:
            size = min(30, budget - start)
            xml = fetch(query, size, start=start)
            root = ET.fromstring(xml)
            if root.tag != f"{ATOM}feed":
                raise ValueError("响应不是 Atom feed")
            entries = root.findall(f"{ATOM}entry")
            if any((entry.findtext(f"{ATOM}title") or "").strip().lower() == "error"
                   for entry in entries):
                raise ValueError("arXiv 返回错误条目")
            total_text = root.findtext(f"{opensearch}totalResults")
            start_text = root.findtext(f"{opensearch}startIndex")
            if total_text is None or start_text is None:
                raise ValueError("响应缺少分页元数据")
            total, returned_start = int(total_text), int(start_text)
            if total < 0 or returned_start != start or len(entries) > size or start + len(entries) > total:
                raise ValueError("响应分页元数据不一致")
            if report["total_results"] is not None and total != report["total_results"]:
                raise ValueError("分页过程中结果总数发生变化")
            report["total_results"] = total
            page = parse(xml)
            stamps = []
            for paper in page:
                identifier = paper["arxiv_id"]
                if not identifier or identifier in observed_ids:
                    raise ValueError("结果缺少 arXiv ID 或分页出现重复，无法确认完整性")
                observed_ids.add(identifier)
                stamp = datetime.strptime(paper["published"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if previous is not None and stamp > previous:
                    raise ValueError("结果未按提交时间降序排列")
                previous = stamp
                stamps.append(stamp)
            if len(page) < size and start + len(page) < total:
                raise ValueError("结果页提前结束，无法确认完整性")
            report["pages"] += 1
            report["fetched"] += len(page)
            papers.extend(p for p, stamp in zip(page, stamps) if stamp >= cutoff)
            start += len(page)
            if start >= total:
                report.update(complete=True, reason="exhausted")
                break
            if stamps and stamps[-1] < cutoff:
                report.update(complete=True, reason="window_exhausted")
                break
            if start >= budget:
                report.update(truncated=True, reason="budget_exhausted")
                break
    except (RuntimeError, ValueError, ET.ParseError, OverflowError) as exc:
        report.update(failed=True, reason="fetch_or_parse_error", error=str(exc))
    return papers, report


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description="arXiv 检索 + 去重")
    ap.add_argument("--days", type=int, default=None, help="时间窗（天），默认读 profile")
    ap.add_argument("--max", type=int, default=90, help="总抓取预算（默认 90，每页最多 30）")
    ap.add_argument("--out", default="", help="输出 JSON 路径")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    ap.add_argument("--no-dedup", action="store_true", help="跳过去重（调试用）")
    ap.add_argument("--doctor", action="store_true", help="检查配置与台账，不执行检索")
    ap.add_argument("--online", action="store_true", help="配合 --doctor 检查 arXiv 网络")
    args = ap.parse_args()

    if args.doctor:
        return doctor_main(["--profile", str(PROFILE), "--venues", str(VENUES)] +
                           (["--online"] if args.online else []))
    if args.online:
        ap.error("--online 只能与 --doctor 一起使用")
    try:
        profile = load_profile()
        data = resolve_data_dir(profile, SKILL_ROOT)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    days = args.days if args.days is not None else int(profile.get("window_days") or 7)
    if days < 1 or args.max < 1:
        ap.error("--days 和 --max 必须为正整数")

    query = build_query(profile)
    if not query:
        print(
            "配置缺失：references/profile.yaml 未填写 direction.keywords。\n"
            "请先完成配置访谈（见 profile.yaml 头部注释），再运行。",
            file=sys.stderr,
        )
        return 2

    papers, search_report = search_pages(query, days, args.max)
    if search_report["failed"]:
        print(f"检索失败（保留已抓取结果）：{search_report['error']}", file=sys.stderr)
    elif search_report["truncated"]:
        print("检索达到总预算，结果可能被截断。", file=sys.stderr)

    if not args.no_dedup:
        pushed = load_ledger(PUSHED if PUSHED != _DEFAULT_PUSHED else data / 'pushed.csv')
        seen = load_ledger(SEEN if SEEN != _DEFAULT_SEEN else data / 'seen.csv')
        fingerprint = venue_fingerprint(VENUES)
        kept, skipped = [], {"pushed": 0, "seen": 0}
        for p in papers:
            if in_pushed(p, pushed):
                skipped["pushed"] += 1
                continue
            if seen_recent(p, seen, fingerprint=fingerprint) is not None:
                skipped["seen"] += 1
                continue
            kept.append(p)
        papers = kept
        print(
            f"去重：跳过已推送 {skipped['pushed']} 篇、30 天内已评估 {skipped['seen']} 篇",
            file=sys.stderr,
        )

    payload = {"query": query, "days": days, "count": len(papers), "papers": papers,
               "truncated": search_report["truncated"], "search_report": search_report}

    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已写入 {args.out}（{len(papers)} 篇）", file=sys.stderr)

    if args.json or not args.out:
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return 3 if search_report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
