"""待推优先级队列、每日限额、>30 积压告警与三账去重集成测试。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts import arxiv_search, dedup, doctor
from scripts.config import parse_yaml, validate_config


class QueueConfigAndDoctorTests(unittest.TestCase):
    def test_queue_warn_threshold_validation(self):
        base = 'keywords: [NLP]\narxiv_categories: [cs.CL]\n'
        venues = parse_yaml('venues: [NeurIPS]\n')
        self.assertEqual(validate_config(parse_yaml(base + 'queue_warn_threshold: 30\n'), venues), [])
        self.assertEqual(validate_config(parse_yaml(base), venues), [])
        for bad in ('queue_warn_threshold: 0\n', 'queue_warn_threshold: -5\n',
                    'queue_warn_threshold: 30.5\n', 'queue_warn_threshold: true\n',
                    'queue_warn_threshold: "30"\n'):
            with self.subTest(bad=bad):
                errors = validate_config(parse_yaml(base + bad), venues)
                self.assertTrue(any('queue_warn_threshold' in e for e in errors))

    def test_doctor_validates_queue_json_when_present(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / 'profile.yaml'
            venues = root / 'venues.yaml'
            profile.write_text(
                f'keywords: [NLP]\narxiv_categories: [cs.CL]\ndata_dir: "{root}"\nqueue_warn_threshold: 2\n',
                encoding='utf-8',
            )
            venues.write_text('venues: [NeurIPS]\n', encoding='utf-8')
            (root / 'pushed.csv').write_text(
                'arxiv_id,doi,title_norm,title,venue,venue_year,pushed_at,score\n', encoding='utf-8'
            )
            (root / 'seen.csv').write_text(
                'arxiv_id,doi,title_norm,title,reason,first_seen,last_checked\n', encoding='utf-8'
            )

            # queue.json 尚未创建时体检仍通过
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(doctor.main(['--profile', str(profile), '--venues', str(venues)]), 0)
            res = json.loads(out.getvalue())
            self.assertTrue(res['ledger']['queue.json']['ok'])
            self.assertFalse(res['ledger']['queue.json']['exists'])

            # 合法 queue.json 且超过阈值 2
            (root / 'queue.json').write_text(json.dumps({
                'schema_version': 1,
                'items': [
                    {'title': f'Paper {i}', 'relevance': 8, 'venue_score': 1.0}
                    for i in range(3)
                ],
            }), encoding='utf-8')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(doctor.main(['--profile', str(profile), '--venues', str(venues)]), 0)
            res = json.loads(out.getvalue())
            self.assertTrue(res['ledger']['queue.json']['ok'])
            self.assertEqual(res['ledger']['queue.json']['count'], 3)
            self.assertTrue(res['ledger']['queue.json']['warn_exceeded'])

            # 损坏 queue.json 被体检拦截
            (root / 'queue.json').write_text('{"schema_version": 1, "items": [{"title": ""}]}', encoding='utf-8')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(doctor.main(['--profile', str(profile), '--venues', str(venues)]), 1)
            res = json.loads(out.getvalue())
            self.assertFalse(res['ledger']['queue.json']['ok'])
            self.assertTrue(any('队列错误' in e for e in res['errors']))


class QueueWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profile.yaml'
        self.venues = self.root / 'venues.yaml'
        self.profile.write_text(
            'keywords: [NLP]\narxiv_categories: [cs.CL]\nmax_papers: 5\nqueue_warn_threshold: 30\n',
            encoding='utf-8',
        )
        self.venues.write_text('venues: [NeurIPS, ICML]\n', encoding='utf-8')
        for obj, name, value in (
            (dedup, 'SKILL_ROOT', self.root),
            (dedup, 'PROFILE', self.profile),
            (arxiv_search, 'SKILL_ROOT', self.root),
            (arxiv_search, 'PROFILE', self.profile),
            (arxiv_search, 'VENUES', self.venues),
        ):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_dedup(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['dedup.py', *args]), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dedup.main()
        text = out.getvalue().strip()
        return code, (json.loads(text) if text else {}), err.getvalue()

    def test_priority_order_and_multiday_carryover(self):
        # Day 1: 8 篇合格论文入队，其中包含不同相关度、venue 分数、发表时间
        batch = self.root / 'day1.json'
        batch.write_text(json.dumps([
            {'arxiv_id': '2611.00001', 'title': 'Edge Paper', 'relevance': 6, 'venue_score': 1.0, 'published': '2026-11-26', 'venue': 'NeurIPS', 'venue_year': '2026', 'queued_at': '2026-11-26'},
            {'arxiv_id': '2611.00002', 'title': 'Mid Preprint', 'relevance': 8, 'venue_score': 0.5, 'published': '2026-11-26', 'venue': 'preprint', 'queued_at': '2026-11-26'},
            {'arxiv_id': '2611.00003', 'title': 'Mid Venue Older', 'relevance': 8, 'venue_score': 1.0, 'published': '2026-11-20', 'venue': 'ICML', 'venue_year': '2026', 'queued_at': '2026-11-26'},
            {'arxiv_id': '2611.00004', 'title': 'Mid Venue Newer', 'relevance': 8, 'venue_score': 1.0, 'published': '2026-11-25', 'venue': 'NeurIPS', 'venue_year': '2026', 'queued_at': '2026-11-26'},
            {'arxiv_id': '2611.00005', 'title': 'Top Paper', 'relevance': 9, 'venue_score': 0.5, 'published': '2026-11-24', 'venue': 'preprint', 'summary': 'Key finding', 'code_url': 'https://github.com/a/b', 'queued_at': '2026-11-26'},
            {'arxiv_id': '2611.00006', 'title': 'Tie FIFO Second', 'relevance': 7, 'venue_score': 1.0, 'published': '2026-11-25', 'venue': 'NeurIPS', 'venue_year': '2026', 'queued_at': '2026-11-26'},
            {'arxiv_id': '2611.00007', 'title': 'Tie FIFO First', 'relevance': 7, 'venue_score': 1.0, 'published': '2026-11-25', 'venue': 'NeurIPS', 'venue_year': '2026', 'queued_at': '2026-11-25'},
            {'arxiv_id': '2611.00008', 'title': 'Lowest Edge', 'relevance': 5, 'venue_score': 0.5, 'published': '2026-11-26', 'venue': 'preprint', 'queued_at': '2026-11-26'},
        ]), encoding='utf-8')

        day1 = date(2026, 11, 26)
        with patch.object(dedup, 'date') as mock_date:
            mock_date.today.return_value = day1
            code, res, _ = self.run_dedup('enqueue', '--json-in', str(batch))
            self.assertEqual(code, 0)
            self.assertEqual(res['enqueued'], 8)
            self.assertEqual(res['queue_total'], 8)

            # dry-run 不改文件
            code, preview, _ = self.run_dedup('pop', '--dry-run')
            self.assertEqual(code, 0)
            self.assertEqual(preview['delivered_count'], 5)
            self.assertEqual(
                [p['title'] for p in preview['delivered']],
                ['Top Paper', 'Mid Venue Newer', 'Mid Venue Older', 'Mid Preprint', 'Tie FIFO First'],
            )
            _, status_after_dry, _ = self.run_dedup('queue-status')
            self.assertEqual(status_after_dry['queue_total'], 8)

            # 实际弹出 5 篇，剩余 3 篇留到次日
            code, popped, _ = self.run_dedup('pop')
            self.assertEqual(code, 0)
            self.assertEqual(popped['delivered_count'], 5)
            self.assertEqual(popped['queue_total'], 3)
            self.assertEqual(popped['remaining_quota'], 0)
            self.assertEqual(popped['delivered'][0]['code_url'], 'https://github.com/a/b')
            self.assertEqual(popped['delivered'][0]['summary'], 'Key finding')

            # 同日再次 pop 配额为 0，不再多推
            code, popped_again, _ = self.run_dedup('pop')
            self.assertEqual(code, 0)
            self.assertEqual(popped_again['delivered_count'], 0)
            self.assertEqual(popped_again['queue_total'], 3)

        # Day 2: 新入队 1 篇 10 分论文，应插队最前；连同昨日剩余 3 篇共推 4 篇
        day2 = date(2026, 11, 27)
        with patch.object(dedup, 'date') as mock_date:
            mock_date.today.return_value = day2
            code, res, _ = self.run_dedup(
                'enqueue', '--title', 'Breakthrough Paper', '--arxiv-id', '2611.00099',
                '--relevance', '10', '--venue', 'NeurIPS', '--year', '2026', '--venue-score', '1.0',
                '--published', '2026-11-27',
            )
            self.assertEqual(code, 0)
            self.assertEqual(res['queue_total'], 4)
            self.assertEqual(res['remaining_quota'], 5)

            code, popped2, _ = self.run_dedup('pop')
            self.assertEqual(code, 0)
            self.assertEqual(popped2['delivered_count'], 4)
            self.assertEqual(
                [p['title'] for p in popped2['delivered']],
                ['Breakthrough Paper', 'Tie FIFO Second', 'Edge Paper', 'Lowest Edge'],
            )
            self.assertEqual(popped2['queue_total'], 0)

    def test_unlimited_queue_and_30_warn_threshold(self):
        items = []
        for i in range(36):
            rel = 9 if i < 4 else (7 if i < 16 else 5)
            items.append({
                'arxiv_id': f'2611.{10000 + i}',
                'title': f'Queued Paper {i}',
                'relevance': rel,
                'venue_score': 1.0 if i % 2 == 0 else 0.5,
                'published': '2026-11-20',
                'queued_at': '2026-11-20' if i == 0 else '2026-11-27',
            })
        batch = self.root / 'many.json'
        batch.write_text(json.dumps(items), encoding='utf-8')

        today = date(2026, 11, 27)
        with patch.object(dedup, 'date') as mock_date:
            mock_date.today.return_value = today
            code, res, _ = self.run_dedup('enqueue', '--json-in', str(batch))
            self.assertEqual(code, 0)
            self.assertEqual(res['enqueued'], 36)
            self.assertEqual(res['queue_total'], 36)
            self.assertTrue(res['warn_exceeded'])
            self.assertEqual(res['by_tier'], {'high_9_10': 4, 'mid_7_8': 12, 'edge_5_6': 20, 'other': 0})
            self.assertEqual(res['oldest_queued_at'], '2026-11-20')
            self.assertEqual(res['oldest_days'], 7)

            # 弹出 5 篇后剩 31 篇 -> 仍超过 30，触发告警
            code, popped, _ = self.run_dedup('pop')
            self.assertEqual(code, 0)
            self.assertEqual(popped['delivered_count'], 5)
            self.assertEqual(popped['queue_total'], 31)
            self.assertTrue(popped['warn_exceeded'])

            # 再弹出 1 篇后恰好 30 篇 -> 不再触发 >30 告警
            code, popped_one, _ = self.run_dedup('pop', '--limit', '1')
            self.assertEqual(code, 0)
            self.assertEqual(popped_one['queue_total'], 30)
            self.assertFalse(popped_one['warn_exceeded'])

    def test_three_ledger_dedup_between_check_enqueue_seen_and_arxiv_search(self):
        # 先把一篇记入 seen.csv（模拟曾无开源，后复核通过）
        code, _, _ = self.run_dedup('reject', '--title', 'Revived Paper', '--arxiv-id', '2611.20001', '--reason', 'no_code')
        self.assertEqual(code, 0)

        # 入队后自动从 seen.csv 移除
        code, res, _ = self.run_dedup(
            'enqueue', '--title', 'Revived Paper', '--arxiv-id', '2611.20001', '--relevance', '8'
        )
        self.assertEqual(code, 0)
        self.assertEqual(res['status'], 'enqueued')

        # dedup check 对队列内论文返回退出码 4 (queued)
        code, chk, _ = self.run_dedup('check', '--title', 'Different Title', '--arxiv-id', '2611.20001')
        self.assertEqual(code, 4)
        self.assertEqual(chk['status'], 'queued')

        # 重复入队同一篇（仅标题一致）被拦截
        code, dup, _ = self.run_dedup('enqueue', '--title', 'revived paper!', '--relevance', '9')
        self.assertEqual(code, 1)
        self.assertEqual(dup['status'], 'already_queued')

        # arxiv_search 同步跳过已在 queue.json 的论文
        report = {'failed': False, 'truncated': False}
        papers = [
            {'title': 'Revived Paper', 'arxiv_id': '2611.20001'},
            {'title': 'Brand New Paper', 'arxiv_id': '2611.20002'},
        ]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(arxiv_search, 'search_pages', return_value=(papers, report)), \
             patch.object(sys, 'argv', ['arxiv_search.py', '--json']), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(arxiv_search.main(), 0)
        self.assertEqual([p['title'] for p in json.loads(out.getvalue())['papers']], ['Brand New Paper'])
        self.assertIn('待推队列 1 篇', err.getvalue())

        # 直接 push 也会把队列里同篇同步移除
        code, _, _ = self.run_dedup('push', '--title', 'Revived Paper', '--arxiv-id', '2611.20001')
        self.assertEqual(code, 0)
        code, st, _ = self.run_dedup('queue-status')
        self.assertEqual(code, 0)
        self.assertEqual(st['queue_total'], 0)


if __name__ == '__main__':
    unittest.main()
