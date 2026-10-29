"""doctor、写台账与检索必须对同一画像使用相同目录。"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import arxiv_search, dedup, doctor


class DataDirectoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profile.yaml'
        self.venues = self.root / 'venues.yaml'
        self.venues.write_text('venues: [ICML]\n', encoding='utf-8')
        for obj, name, value in ((dedup, 'SKILL_ROOT', self.root), (dedup, 'PROFILE', self.profile),
                                 (arxiv_search, 'SKILL_ROOT', self.root), (arxiv_search, 'PROFILE', self.profile),
                                 (arxiv_search, 'VENUES', self.venues), (doctor, 'ROOT', self.root)):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)

    def profile_text(self, extra=''):
        self.profile.write_text('keywords: [NLP]\narxiv_categories: [cs.CL]\n' + extra, encoding='utf-8')

    def dedup_command(self, *args):
        with patch.object(sys, 'argv', ['dedup.py', *args]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return dedup.main()

    def test_configured_and_default_directories(self):
        for value in ('data_dir: nested/store\n', f'data_dir: "{self.root / "absolute"}"\n', ''):
            with self.subTest(value=value):
                self.profile_text(value)
                data = (self.root / 'nested/store' if 'nested' in value else
                        self.root / 'absolute' if 'absolute' in value else self.root / 'data')
                self.assertEqual(self.dedup_command('push', '--title', 'pushed', '--arxiv-id', '1234.5678'), 0)
                self.assertEqual(self.dedup_command('reject', '--title', 'seen', '--reason', 'no_code'), 0)
                self.assertTrue((data / 'pushed.csv').exists())
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(doctor.main(['--profile', str(self.profile), '--venues', str(self.venues)]), 0)
                self.assertEqual(Path(json.loads(output.getvalue())['ledger']['pushed.csv']['path']), data / 'pushed.csv')
                report = {'failed': False, 'truncated': False}
                papers = [{'title': 'pushed', 'arxiv_id': '1234.5678'}, {'title': 'seen'}, {'title': 'new'}]
                output = io.StringIO()
                with patch.object(arxiv_search, 'search_pages', return_value=(papers, report)), \
                     patch.object(sys, 'argv', ['arxiv_search.py', '--json']), \
                     contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(arxiv_search.main(), 0)
                self.assertEqual([p['title'] for p in json.loads(output.getvalue())['papers']], ['new'])

    def test_invalid_config_fails_before_io_or_network(self):
        for text in ('data_dir: [broken\n', 'data_dir: []\n', 'data_dir: ""\n'):
            with self.subTest(text=text):
                self.profile_text(text)
                self.assertEqual(self.dedup_command('push', '--title', 'no write'), 2)
                with patch.object(arxiv_search, 'search_pages', side_effect=AssertionError('network')), \
                     patch.object(sys, 'argv', ['arxiv_search.py']), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(arxiv_search.main(), 2)
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(doctor.main(['--profile', str(self.profile), '--venues', str(self.venues)]), 2)
                self.assertFalse((self.root / 'data').exists())

    def test_bom_first_column_recognized_by_both_consumers(self):
        self.profile_text('data_dir: custom\n')
        data = self.root / 'custom'
        data.mkdir()
        (data / 'pushed.csv').write_text('arxiv_id,doi,title_norm,title,venue,venue_year,pushed_at,score\n1234.5678,,,,,,,\n', encoding='utf-8-sig')
        (data / 'seen.csv').write_text('arxiv_id,doi,title_norm,title,reason,first_seen,last_checked\n', encoding='utf-8-sig')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(doctor.main(['--profile', str(self.profile), '--venues', str(self.venues)]), 0)
        self.assertEqual(self.dedup_command('check', '--title', 'different', '--arxiv-id', '1234.5678'), 1)
        report = {'failed': False, 'truncated': False}
        output = io.StringIO()
        with patch.object(arxiv_search, 'search_pages', return_value=([{'title': 'different', 'arxiv_id': '1234.5678'}], report)), \
             patch.object(sys, 'argv', ['arxiv_search.py', '--json']), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(arxiv_search.main(), 0)
        self.assertEqual(json.loads(output.getvalue())['count'], 0)


if __name__ == '__main__':
    unittest.main()
