"""独立测试 arXiv 分页、完整性和限速，不访问网络。"""
import contextlib
import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts import arxiv_search as arxiv


def feed(start, total, stamps):
    entries = ''.join(
        f'<entry><id>http://arxiv.org/abs/{start+i:04d}.12345v1</id>'
        f'<title>paper {start+i}</title><published>{stamp}</published></entry>'
        for i, stamp in enumerate(stamps)
    )
    return (f'<feed xmlns="http://www.w3.org/2005/Atom" '
            f'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
            f'<opensearch:totalResults>{total}</opensearch:totalResults>'
            f'<opensearch:startIndex>{start}</opensearch:startIndex>{entries}</feed>')


def stamp(days=0):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')


class PaginationTests(unittest.TestCase):
    def test_budget_and_offsets(self):
        calls = []
        def get(query, size, *, start):
            calls.append((start, size))
            return feed(start, 120, [stamp()] * size)
        with patch.object(arxiv, 'fetch', side_effect=get):
            papers, report = arxiv.search_pages('q', 7, 65)
        self.assertEqual(calls, [(0, 30), (30, 30), (60, 5)])
        self.assertEqual(len(papers), 65)
        self.assertTrue(report['truncated'])
        self.assertFalse(report['complete'])

    def test_window_stop_and_exhaustion(self):
        with patch.object(arxiv, 'fetch', return_value=feed(0, 50, [stamp()] * 29 + [stamp(8)])) as fetch:
            papers, report = arxiv.search_pages('q', 7, 90)
        self.assertEqual(len(papers), 29)
        self.assertEqual(report['reason'], 'window_exhausted')
        self.assertTrue(report['complete'])
        self.assertEqual(fetch.call_count, 1)
        with patch.object(arxiv, 'fetch', return_value=feed(0, 2, [stamp()] * 2)):
            _, report = arxiv.search_pages('q', 7, 90)
        self.assertEqual(report['reason'], 'exhausted')
        self.assertTrue(report['complete'])

    def test_failure_retains_previous_page(self):
        with patch.object(arxiv, 'fetch', side_effect=[feed(0, 50, [stamp()] * 30), RuntimeError('offline')]):
            papers, report = arxiv.search_pages('q', 7, 90)
        self.assertEqual(len(papers), 30)
        self.assertTrue(report['failed'])
        self.assertFalse(report['complete'])
        self.assertIn('offline', report['error'])

    def test_untrustworthy_pages_fail(self):
        cases = [
            '<bad/>',
            '<feed xmlns="http://www.w3.org/2005/Atom"/>',
            feed(0, 50, [stamp()] * 2),
            feed(1, 30, [stamp()] * 30),
            feed(0, 2, [stamp(8), stamp()]),
            feed(0, 1, ['bad']),
        ]
        for xml in cases:
            with self.subTest(xml=xml[:50]), patch.object(arxiv, 'fetch', return_value=xml):
                papers, report = arxiv.search_pages('q', 7, 90)
                self.assertTrue(report['failed'])
                self.assertFalse(report['complete'])
                self.assertEqual(papers, [])

    def test_main_emits_partial_json_on_failure(self):
        out = io.StringIO()
        with patch.object(arxiv, 'load_profile', return_value={'keywords': ['x']}), \
             patch.object(arxiv, 'fetch', side_effect=[feed(0, 31, [stamp()] * 30), RuntimeError('offline')]), \
             patch.object(sys, 'argv', ['arxiv_search.py', '--no-dedup']), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = arxiv.main()
        payload = json.loads(out.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(payload['count'], 30)
        self.assertTrue(payload['search_report']['failed'])

    def test_fetch_rate_limit_between_pages(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def getcode(self): return 200
            def read(self): return b'ok'
        class Opener:
            def open(self, *args, **kwargs): return Response()
        with patch.object(arxiv, '_last_request', None), \
             patch.object(arxiv.urllib.request, 'build_opener', return_value=Opener()), \
             patch.object(arxiv.time, 'monotonic', side_effect=[0, 1, 3]), \
             patch.object(arxiv.time, 'sleep') as sleep:
            arxiv.fetch('q', 30, start=0)
            arxiv.fetch('q', 30, start=30)
        sleep.assert_called_once_with(2)


if __name__ == '__main__':
    unittest.main()
