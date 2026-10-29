import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.config import ConfigError, load_config, parse_yaml, validate_config
from scripts import doctor

PROFILE = '''direction:
  name: "A # B"
  description: 'it''s: useful'
  keywords:
    - "language, model"
    - NLP
arxiv_categories: [cs.CL, cs.AI]
conference_years: [2025, 2026]
exclude: []
window_days: 7
max_papers: 5
min_relevance: 5
'''
VENUES = '''default_score: 1.0
preprint_score: 0.5
venues:
  - name: NeurIPS
    aliases: [NIPS, "A, B # C"]
    score: 1.0
    note: 'test: value'
  - name: ICML
    aliases:
      - 'International Conference'
'''


class ParserTests(unittest.TestCase):
    def test_nested_and_quoted(self):
        profile, venues = parse_yaml(PROFILE), parse_yaml(VENUES)
        self.assertEqual(profile['direction']['description'], "it's: useful")
        self.assertEqual(profile['direction']['name'], 'A # B')
        self.assertEqual(profile['direction']['keywords'], ['language, model', 'NLP'])
        self.assertEqual(venues['venues'][0]['aliases'][1], 'A, B # C')
        self.assertEqual(venues['venues'][1]['aliases'], ['International Conference'])
        self.assertEqual(validate_config(profile, venues), [])

    def test_legacy_keyword_formats_and_optional_years(self):
        for keyword_line in ('keywords: [NLP]', 'direction.keywords: [NLP]', 'direction:\n  keywords: [NLP]'):
            with self.subTest(keyword_line=keyword_line), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'profile.yaml').write_text(keyword_line + '\narxiv_categories: [cs.CL]\n', encoding='utf-8')
                (root / 'venues.yaml').write_text('venues:\n  - name: Nature\n', encoding='utf-8')
                profile, venues = load_config(root / 'profile.yaml', root / 'venues.yaml')
                self.assertEqual(profile['direction']['keywords'], ['NLP'])
                self.assertEqual(validate_config(profile, venues), [])

    def test_string_venue_lists_and_mapping_normalization(self):
        for contents in ('venues: [NeurIPS, "Nature"]\n', 'venues:\n  - NeurIPS\n  - "Nature"\n'):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'profile.yaml').write_text('keywords: [NLP]\narxiv_categories: [cs.CL]\n', encoding='utf-8')
                (root / 'venues.yaml').write_text(contents, encoding='utf-8')
                _, venues = load_config(root / 'profile.yaml', root / 'venues.yaml')
                self.assertEqual(venues['venues'], [{'name': 'NeurIPS'}, {'name': 'Nature'}])
        self.assertTrue(validate_config(parse_yaml(PROFILE), {'venues': ['']}))

    def test_conflicting_keyword_formats_are_rejected(self):
        profile = parse_yaml('keywords: [A]\ndirection.keywords: [B]\narxiv_categories: [cs.CL]\n')
        self.assertTrue(any('不一致' in error for error in validate_config(profile, parse_yaml(VENUES))))

    def test_unsupported_or_malformed_rejected(self):
        for text in ('x: {a: b}', 'x: &anchor hi', 'x: *anchor', 'x: |\n  hi',
                     'x: [a, b', 'x: a\nx: b', 'x:\n  a: b\n c: d',
                     'x: "broken', 'x: [a,,b]', 'x: a\n  bad: b', 'x: yes'):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                parse_yaml(text)

    def test_validation_is_fail_closed(self):
        profile, venues = parse_yaml(PROFILE), parse_yaml(VENUES)
        profile['direction']['keywords'] = []
        profile['arxiv_categories'] = ['']
        venues['venues'][0]['score'] = -1
        errors = validate_config(profile, venues)
        self.assertTrue(any('keywords' in e for e in errors))
        self.assertTrue(any('arxiv_categories' in e for e in errors))
        self.assertTrue(any('score' in e for e in errors))

    def test_empty_files_rejected_without_user_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'profile.yaml').write_text('direction:\n  name: ""\n  keywords: []\n', encoding='utf-8')
            (root / 'venues.yaml').write_text('venues: []\n', encoding='utf-8')
            with self.assertRaises(ConfigError):
                load_config(root / 'profile.yaml', root / 'venues.yaml')


class DoctorTests(unittest.TestCase):
    def test_offline_does_not_open_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'profile.yaml').write_text(PROFILE + f'data_dir: "{root}"\n', encoding='utf-8')
            (root / 'venues.yaml').write_text(VENUES, encoding='utf-8')
            (root / 'pushed.csv').write_text('arxiv_id,doi,title_norm,title,venue,venue_year,pushed_at,score\n', encoding='utf-8')
            (root / 'seen.csv').write_text('arxiv_id,doi,title_norm,title,reason,first_seen,last_checked\n', encoding='utf-8')
            output = io.StringIO()
            with patch('urllib.request.build_opener', side_effect=AssertionError('network')), contextlib.redirect_stdout(output):
                self.assertEqual(doctor.main(['--profile', str(root / 'profile.yaml'), '--venues', str(root / 'venues.yaml')]), 0)
            result = json.loads(output.getvalue())
            self.assertTrue(result['configured'])
            self.assertTrue(result['ledger']['seen.csv']['ok'])  # 旧表头无 fingerprint
            self.assertEqual(result['network']['status'], 'skipped')
            self.assertEqual(result['errors'], [])
            (root / 'seen.csv').write_text('arxiv_id,title\n', encoding='utf-8')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(doctor.main(['--profile', str(root / 'profile.yaml'), '--venues', str(root / 'venues.yaml')]), 1)
            result = json.loads(output.getvalue())
            self.assertTrue(result['configured'])
            self.assertFalse(result['ledger']['seen.csv']['ok'])
            self.assertTrue(any('表头缺少列' in e for e in result['errors']))

    def test_bad_config_exit(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(doctor.main(['--profile', '/no/such/file']), 2)
        result = json.loads(output.getvalue())
        self.assertFalse(result['configured'])
        self.assertTrue(result['errors'])

    def test_online_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'profile.yaml').write_text(PROFILE + f'data_dir: "{root}"\n', encoding='utf-8')
            (root / 'venues.yaml').write_text(VENUES, encoding='utf-8')
            (root / 'pushed.csv').write_text('arxiv_id,doi,title_norm,title,venue,venue_year,pushed_at,score\n', encoding='utf-8')
            (root / 'seen.csv').write_text('arxiv_id,doi,title_norm,title,reason,first_seen,last_checked\n', encoding='utf-8')
            output = io.StringIO()
            with patch('urllib.request.build_opener', side_effect=OSError('offline')), contextlib.redirect_stdout(output):
                self.assertEqual(doctor.main(['--profile', str(root / 'profile.yaml'), '--venues', str(root / 'venues.yaml'), '--online']), 1)
            result = json.loads(output.getvalue())
            self.assertTrue(result['configured'])
            self.assertEqual(result['network']['status'], 'error')
            self.assertTrue(result['errors'])


if __name__ == '__main__':
    unittest.main()
