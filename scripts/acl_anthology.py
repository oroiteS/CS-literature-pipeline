#!/usr/bin/env python3
"""ACL Anthology volume-link discovery and paper-link extraction.

The caller supplies an event/index HTML page or an individual volume page.
Discovery is limited to links actually present in the supplied HTML: an event
page may be incomplete, so an empty or partial result is not proof of exhaustion.
This module neither performs a venue scan nor manages its checkpoints.
"""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlsplit

BASE = 'https://aclanthology.org/'
VOLUME = re.compile(r'^(?P<year>\d{4})\.(?P<family>[a-z0-9]+)-(?P<track>[a-z0-9][a-z0-9-]*)$', re.I)
PAPER = re.compile(r'^([a-z0-9][a-z0-9.-]*)\.(\d+)$', re.I)


def _path(href: str) -> str | None:
    parts = urlsplit(urljoin(BASE, href))
    if parts.scheme not in ('https', 'http') or parts.hostname not in ('aclanthology.org', 'www.aclanthology.org'):
        return None
    return parts.path.strip('/')


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            href = dict(attrs).get('href')
            if href is not None:
                self.current = [href, []]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self.current is not None:
            href, text = self.current
            self.links.append((href, ' '.join(''.join(text).split())))
            self.current = None


def _links(html: str):
    parser = _Links()
    parser.feed(html)
    parser.close()
    return parser.links


def discover_volumes(html: str, year: int, family: str) -> list[dict]:
    """Return linked year.family-track and year.findings-family volumes.

    Exact family matching prevents ACL from accidentally including EACL/NAACL.
    Caller must decide which tracks belong to its whitelist and whether the
    supplied index covers all volumes; no completeness is inferred here.
    """
    if not isinstance(year, int) or isinstance(year, bool) or not 1 <= year <= 9999:
        raise ValueError('invalid year')
    if not isinstance(family, str) or not re.fullmatch(r'[a-z0-9]+', family, re.I):
        raise ValueError('invalid family')
    found = {}
    for href, title in _links(html):
        path = _path(href)
        if path is None:
            continue
        slug = path.removeprefix('volumes/')
        m = VOLUME.fullmatch(slug)
        if not m or int(m['year']) != year:
            continue
        prefix, suffix = m['family'].lower(), m['track'].lower()
        if prefix == family.lower():
            track = suffix
        elif prefix == 'findings' and suffix == family.lower():
            track = 'findings'
        else:
            continue
        slug = slug.lower()
        found.setdefault(slug, {'volume': slug, 'track': track,
                                'url': urljoin(BASE, 'volumes/' + slug + '/'), 'label': title})
    return list(found.values())


def parse_papers(html: str, volume: str) -> list[dict]:
    """Extract distinct links whose Anthology paper ID belongs to volume.

    Titles are link text, which may be missing or non-title navigation text;
    downstream users must verify against the paper's canonical metadata.
    """
    if not isinstance(volume, str) or not VOLUME.fullmatch(volume):
        raise ValueError('invalid volume')
    found = {}
    for href, title in _links(html):
        path = _path(href)
        if path is None or '/' in path:
            continue
        m = PAPER.fullmatch(path)
        if m and m.group(1).lower() == volume.lower():
            paper_id = path.lower()
            if paper_id not in found or (not found[paper_id]['title'] and title):
                found[paper_id] = {'id': paper_id, 'title': title, 'url': urljoin(BASE, paper_id + '/')}
    return list(found.values())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    discover = sub.add_parser('discover', help='discover links from a saved event/index HTML file')
    discover.add_argument('--html', required=True)
    discover.add_argument('--year', type=int, required=True)
    discover.add_argument('--family', required=True)
    papers = sub.add_parser('papers', help='extract paper links from a saved volume HTML file')
    papers.add_argument('--html', required=True)
    papers.add_argument('--volume', required=True)
    args = parser.parse_args(argv)
    try:
        html = Path(args.html).read_text(encoding='utf-8')
        result = discover_volumes(html, args.year, args.family) if args.action == 'discover' else parse_papers(html, args.volume)
        print(json.dumps({'items': result, 'count': len(result), 'complete': None}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f'ACL Anthology: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
