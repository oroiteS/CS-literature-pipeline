#!/usr/bin/env python3
"""Explicit, version-keyed venue scan checkpoints; no scanner is run implicitly.

Library: make_key(...), load_state(path, key), save_state(path, key, status,
    cursor=..., data=..., error=...). Missing keys return an unscanned record.
CLI: get/set/list; use --help. All versions are caller-provided identifiers,
not automatically calculated content hashes. Serial writers only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

STATUSES = frozenset(('unscanned', 'partial', 'interrupted', 'failed', 'complete'))
KEY_FIELDS = ('venue', 'year', 'volume', 'config_version', 'roster_version')


def make_key(venue: str, year: int, volume: str, config_version: str, roster_version: str) -> dict:
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise ValueError('year must be an integer between 1 and 9999')
    values = (venue, volume, config_version, roster_version)
    if any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError('venue, volume, config_version and roster_version must be non-empty strings')
    return dict(zip(KEY_FIELDS, (venue.strip(), year, volume.strip(), config_version.strip(), roster_version.strip())))


def _key(key: dict) -> dict:
    if not isinstance(key, dict) or set(key) != set(KEY_FIELDS):
        raise ValueError('invalid checkpoint key')
    return make_key(**key)


def _read(path: Path) -> dict:
    if not path.exists():
        return {'schema_version': 1, 'records': []}
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or obj.get('schema_version') != 1 or not isinstance(obj.get('records'), list):
        raise ValueError('invalid venue state file')
    seen = set()
    for rec in obj['records']:
        if not isinstance(rec, dict) or rec.get('status') not in STATUSES:
            raise ValueError('invalid venue state record')
        key = _key(rec.get('key'))
        ident = json.dumps(key, sort_keys=True)
        if ident in seen:
            raise ValueError('duplicate venue state key')
        seen.add(ident)
    return obj


def load_state(path: str | Path, key: dict) -> dict:
    key = _key(key)
    for rec in _read(Path(path))['records']:
        if rec['key'] == key:
            return rec
    return {'key': key, 'status': 'unscanned', 'cursor': None, 'data': {}, 'error': None}


def list_states(path: str | Path) -> list[dict]:
    return _read(Path(path))['records']


def save_state(path: str | Path, key: dict, status: str, *, cursor=None, data=None, error=None) -> dict:
    """Replace one checkpoint atomically. Caller must serialize concurrent writers.

    cursor/data must be JSON serializable; store a stable resume cursor and counts
    only after their corresponding output has been safely persisted. Completion is
    the caller's explicit assertion, never inferred from an empty response.
    """
    key = _key(key)
    if status not in STATUSES:
        raise ValueError('invalid status')
    if data is not None and not isinstance(data, dict):
        raise ValueError('data must be a dict')
    if error is not None and not isinstance(error, str):
        raise ValueError('error must be a string or None')
    path = Path(path)
    obj = _read(path)
    record = {'key': key, 'status': status, 'cursor': cursor, 'data': data if data is not None else {},
              'error': error, 'updated_at': datetime.now(timezone.utc).isoformat()}
    obj['records'] = [r for r in obj['records'] if r['key'] != key] + [record]
    payload = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.venue-state-', delete=False) as f:
            name = f.name
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        name = None
    finally:
        if name is not None:
            os.unlink(name)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, help='state JSON path')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('get', 'set'):
        p = sub.add_parser(action)
        p.add_argument('--venue', required=True)
        p.add_argument('--year', required=True, type=int)
        p.add_argument('--volume', required=True)
        p.add_argument('--config-version', required=True)
        p.add_argument('--roster-version', required=True)
        if action == 'set':
            p.add_argument('--status', required=True, choices=sorted(STATUSES))
            p.add_argument('--cursor-json', default='null')
            p.add_argument('--data-json', default='{}')
            p.add_argument('--error')
    sub.add_parser('list')
    args = parser.parse_args(argv)
    try:
        if args.action == 'list':
            result = list_states(args.state)
        else:
            key = make_key(args.venue, args.year, args.volume, args.config_version, args.roster_version)
            if args.action == 'get':
                result = load_state(args.state, key)
            else:
                result = save_state(args.state, key, args.status, cursor=json.loads(args.cursor_json),
                                    data=json.loads(args.data_json), error=args.error)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f'venue state: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
