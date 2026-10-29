#!/usr/bin/env python3
"""JSON 诊断配置与台账；默认离线，--online 才探测 arXiv。"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from .config import ConfigError, load_config
except ImportError:  # 作为脚本执行
    from config import ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_COLUMNS = {
    'pushed.csv': {'arxiv_id', 'doi', 'title_norm', 'title', 'venue', 'venue_year', 'pushed_at', 'score'},
    # venue_fingerprint 为新增列，旧台账可不含。
    'seen.csv': {'arxiv_id', 'doi', 'title_norm', 'title', 'reason', 'first_seen', 'last_checked'},
}


def resolve_data_dir(profile: dict, root: Path) -> Path:
    """解析台账目录；仅缺失字段时采用默认值。"""
    value = profile.get('data_dir', 'data')
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ConfigError('data_dir 必须是有效的非空路径字符串')
    try:
        data = Path(value)
        return data if data.is_absolute() else root / data
    except (ValueError, OSError) as e:
        raise ConfigError(f'data_dir 无效：{e}') from e


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=ROOT / 'references/profile.yaml')
    parser.add_argument('--venues', type=Path, default=ROOT / 'references/venues.yaml')
    parser.add_argument('--online', action='store_true', help='可选：直接联网探测 arXiv（不使用代理）')
    args = parser.parse_args(argv)
    result = {'configured': False, 'ledger': {}, 'network': {'status': 'skipped'}, 'errors': []}
    try:
        profile, _ = load_config(args.profile, args.venues)
        data = resolve_data_dir(profile, ROOT)
        result['configured'] = True
    except ConfigError as e:
        result['errors'].append(f'配置错误：{e}')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    for name, required in REQUIRED_COLUMNS.items():
        path = data / name
        entry = {'path': str(path), 'ok': False}
        result['ledger'][name] = entry
        try:
            with path.open(encoding='utf-8-sig', newline='') as f:
                header = next(csv.reader(f), [])
            if not header or any(not column for column in header) or len(set(header)) != len(header):
                raise ValueError('表头为空或含重复列')
            missing = sorted(required - set(header))
            if missing:
                raise ValueError('表头缺少列：' + ', '.join(missing))
            entry['ok'] = True
        except (OSError, UnicodeError, ValueError, csv.Error) as e:
            result['errors'].append(f'台账错误：{path}: {e}')
    if args.online:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request = urllib.request.Request('https://export.arxiv.org/api/query?search_query=all%3Atest&start=0&max_results=1', headers={'User-Agent': 'CS-literature-pipeline/1.0 (research; contact: local)'})
            with opener.open(request, timeout=10) as response:
                if response.status != 200:
                    raise OSError(f'HTTP {response.status}')
            result['network']['status'] = 'ok'
        except (OSError, urllib.error.URLError) as e:
            result['network']['status'] = 'error'
            result['errors'].append(f'arXiv 联网探测失败：{e}')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
