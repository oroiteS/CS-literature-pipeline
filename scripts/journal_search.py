#!/usr/bin/env python3
"""Crossref 期刊候选检索（仅元数据，须向期刊官网核验）。

例如：python3 scripts/journal_search.py --journal 'Nature' --start 2024-01-01 \
    --end 2024-12-31 --max-pages 10

按 Crossref 的 query.container-title 发现记录，再对 container-title 作规范化全等
匹配。为保留缺失首次在线日期的记录，不使用 API 日期过滤，分页预算可能耗尽。
本工具无法证明 Crossref 覆盖全部出版商、期刊或文章；即使扫描结束也不代表完整。
可用 --cursor 恢复扫描；Crossref 游标可能过期，过期后需从头重扫并自行去重。
退出码：0 扫描结束，2 参数错误，3 请求/响应失败，4 达到分页预算。
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date
import json
import sys
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API = 'https://api.crossref.org/works'
PAGE_SIZE = 100
COVERAGE = ('仅检索 Crossref 公共 API 收录且搜索命中的元数据；不覆盖所有出版商或文章。'
            '结果不是官方发表证明，须以期刊官网核验首次在线发表日期和文章类型。')


def normalize_name(value):
    """仅做 Unicode、大小写和空白规范化，不删除标点或使用模糊匹配。"""
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def fetch_page(journal, cursor, rows=PAGE_SIZE, timeout=30):
    params = {'query.container-title': journal, 'rows': rows, 'cursor': cursor}
    request = Request(API + '?' + urlencode(params), headers={
        'Accept': 'application/json', 'User-Agent': 'CS-literature-pipeline-journal-search/1.0'})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def online_date(value):
    """Return displayed date, precision and inclusive bounds, without guessing missing parts."""
    if not isinstance(value, dict):
        return None, None, None, None
    parts = value.get('date-parts')
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
        return None, None, None, None
    p = parts[0]
    if not 1 <= len(p) <= 3 or any(type(n) is not int for n in p):
        return None, None, None, None
    try:
        year = p[0]
        month = p[1] if len(p) > 1 else 1
        day = p[2] if len(p) > 2 else 1
        low = date(year, month, day)
        high = (date(year, 12, 31) if len(p) == 1 else
                date(year, month, calendar.monthrange(year, month)[1]) if len(p) == 2 else low)
    except (ValueError, OverflowError):
        return None, None, None, None
    display = f'{year:04d}' + (f'-{month:02d}' if len(p) > 1 else '') + (f'-{day:02d}' if len(p) > 2 else '')
    return display, ('year', 'month', 'day')[len(p) - 1], low, high


def candidate(item, journal, start, end):
    if not isinstance(item, dict) or item.get('type') != 'journal-article':
        return None
    names = item.get('container-title')
    if not isinstance(names, list) or not any(isinstance(n, str) and normalize_name(n) == normalize_name(journal) for n in names):
        return None
    shown, precision, low, high = online_date(item.get('published-online'))
    if low is None:
        status = 'unknown'
    elif high < start or low > end:
        return None
    elif low >= start and high <= end:
        status = 'in_range'
    else:
        status = 'partial_overlap'
    titles = item.get('title')
    title = next((t for t in titles if isinstance(t, str) and t.strip()), None) if isinstance(titles, list) else None
    doi = item.get('DOI') if isinstance(item.get('DOI'), str) else None
    return {'doi': doi, 'title': title, 'journal': names, 'type': 'journal-article',
            'url': item.get('URL') if isinstance(item.get('URL'), str) else None,
            'first_online_date': shown, 'first_online_date_precision': precision,
            'date_status': status, 'date_source': 'Crossref published-online' if shown else None,
            'verification': 'pending_official_verification'}


def search(journal, start, end, max_pages, rows=PAGE_SIZE, fetch=None, cursor='*'):
    """检索页面，保留部分结果；报告范围内、日期未知及部分重叠的候选。"""
    if not isinstance(journal, str) or not normalize_name(journal):
        raise ValueError('期刊名称不能为空')
    if not isinstance(start, date) or not isinstance(end, date) or start > end:
        raise ValueError('日期区间无效')
    if type(max_pages) is not int or max_pages < 1 or type(rows) is not int or not 1 <= rows <= 1000:
        raise ValueError('分页预算必须为正整数，单页大小须为 1–1000')
    if not isinstance(cursor, str) or not cursor.strip():
        raise ValueError('分页游标不能为空')
    fetch = fetch or fetch_page
    papers = []
    visited = {cursor}
    report = {'complete': False, 'truncated': False, 'failed': False, 'pages_fetched': 0,
              'items_scanned': 0, 'reason': None, 'error': None, 'coverage_limit': COVERAGE,
              'next_cursor': cursor,
              'cursor_note': 'Crossref 游标可能过期；过期后须从头重扫并自行去重。续扫应使用相同查询参数。'}
    for _ in range(max_pages):
        try:
            data = fetch(journal, cursor, rows)
            if not isinstance(data, dict) or data.get('status') != 'ok' or not isinstance(data.get('message'), dict):
                raise ValueError('Crossref 响应格式无效')
            message = data['message']
            items = message.get('items')
            if not isinstance(items, list) or len(items) > rows or any(not isinstance(i, dict) for i in items):
                raise ValueError('Crossref items 格式无效')
            next_cursor = message.get('next-cursor')
            if len(items) == rows and (not isinstance(next_cursor, str) or not next_cursor or next_cursor in visited):
                raise ValueError('Crossref 分页游标缺失或重复')
            page_candidates = [p for i in items if (p := candidate(i, journal, start, end)) is not None]
        except Exception as exc:
            report.update(failed=True, reason='request_or_response_error', error=str(exc))
            break
        papers.extend(page_candidates)
        report['pages_fetched'] += 1
        report['items_scanned'] += len(items)
        if len(items) < rows:
            report.update(complete=True, reason='api_results_exhausted', next_cursor=None)
            break
        cursor = next_cursor
        report['next_cursor'] = cursor
        visited.add(cursor)
    else:
        report.update(truncated=True, reason='page_budget_exhausted')
    return papers, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--journal', required=True, help='期刊完整名称；不使用模糊匹配')
    parser.add_argument('--start', required=True, type=date.fromisoformat, help='起始日期 YYYY-MM-DD（含）')
    parser.add_argument('--end', required=True, type=date.fromisoformat, help='结束日期 YYYY-MM-DD（含）')
    parser.add_argument('--max-pages', required=True, type=int, help='最多请求的页数')
    parser.add_argument('--cursor', default='*', help='续扫游标；可能过期，过期需从头重扫并去重')
    args = parser.parse_args(argv)
    try:
        papers, report = search(args.journal, args.start, args.end, args.max_pages, cursor=args.cursor)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({'journal_query': args.journal, 'start': args.start.isoformat(),
                      'end': args.end.isoformat(), 'count': len(papers),
                      'candidates': papers, 'search_report': report}, ensure_ascii=False, indent=2))
    return 3 if report['failed'] else 4 if report['truncated'] else 0


if __name__ == '__main__':
    sys.exit(main())
