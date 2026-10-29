import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


state = load('venue_state')
acl = load('acl_anthology')


class VenueStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'nested' / 'state.json'
        self.key = state.make_key('ACL', 2024, '2024.acl-long', 'config1', 'list1')

    def test_roundtrip_and_version_isolation(self):
        self.assertEqual(state.load_state(self.path, self.key)['status'], 'unscanned')
        for status in ('partial', 'interrupted', 'failed', 'complete'):
            state.save_state(self.path, self.key, status, cursor={'offset': 2}, data={'count': 2}, error='network' if status == 'failed' else None)
            result = state.load_state(self.path, self.key)
            self.assertEqual(result['status'], status)
            self.assertEqual(result['cursor'], {'offset': 2})
        for changed in ({'config_version': 'config2'}, {'roster_version': 'list2'}, {'volume': '2024.acl-short'}, {'year': 2025}, {'venue': 'EMNLP'}):
            key = dict(self.key, **changed)
            self.assertEqual(state.load_state(self.path, key)['status'], 'unscanned')
            state.save_state(self.path, key, 'partial')
        self.assertEqual(len(state.list_states(self.path)), 6)
        self.assertEqual(state.load_state(self.path, self.key)['status'], 'complete')

    def test_validation_does_not_destroy_saved_file(self):
        state.save_state(self.path, self.key, 'partial')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            state.save_state(self.path, self.key, 'pretend')
        with self.assertRaises((TypeError, ValueError)):
            state.save_state(self.path, self.key, 'partial', data={'bad': object()})
        self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text('{bad', encoding='utf-8')
        with self.assertRaises(ValueError):
            state.save_state(self.path, self.key, 'failed')
        self.assertEqual(self.path.read_text(), '{bad')

    def test_cli(self):
        cmd = [sys.executable, str(ROOT / 'scripts/venue_state.py'), '--state', str(self.path)]
        key = ['--venue', 'ACL', '--year', '2024', '--volume', 'main', '--config-version', 'c', '--roster-version', 'r']
        subprocess.run(cmd + ['set'] + key + ['--status', 'interrupted', '--cursor-json', '3'], check=True, capture_output=True)
        result = subprocess.run(cmd + ['get'] + key, check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)['cursor'], 3)
        self.assertEqual(json.loads(result.stdout)['status'], 'interrupted')


class ACLTests(unittest.TestCase):
    def test_multi_volume_and_unquoted_attrs(self):
        html = '''<a href=/volumes/2024.acl-long/>Long</a>
          <a href='/2024.acl-short/'>Short</a>
          <a href="/volumes/2024.findings-acl/">Findings</a>
          <a href=/volumes/2024.acl-long/>duplicate</a>
          <a href=/volumes/2024.naacl-long/>NAACL</a>
          <a href=https://evil.example/volumes/2024.acl-demo/>External</a>
          <a href=/volumes/2023.acl-demo/>Old</a>'''
        result = acl.discover_volumes(html, 2024, 'acl')
        self.assertEqual([x['volume'] for x in result], ['2024.acl-long', '2024.acl-short', '2024.findings-acl'])
        self.assertEqual(result[-1]['track'], 'findings')

    def test_papers_nested_title_exact_volume_and_dedup(self):
        html = '''<a href=/2024.acl-long.1/><b>A &amp; B</b></a>
          <a href='/2024.acl-long.1/'>duplicate</a>
          <a href="https://aclanthology.org/2024.acl-long.2/">Second</a>
          <a href=/2024.acl-short.3/>Other</a>
          <a href=https://evil.example/2024.acl-long.4/>Bad</a>'''
        papers = acl.parse_papers(html, '2024.acl-long')
        self.assertEqual([p['id'] for p in papers], ['2024.acl-long.1', '2024.acl-long.2'])
        self.assertEqual(papers[0]['title'], 'A & B')

    def test_cli_marks_completeness_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'input.html'
            path.write_text('<a href=/2024.acl-long/>volume</a>')
            proc = subprocess.run([sys.executable, str(ROOT / 'scripts/acl_anthology.py'), 'discover', '--html', str(path), '--year', '2024', '--family', 'acl'], check=True, capture_output=True, text=True)
            payload = json.loads(proc.stdout)
            self.assertIsNone(payload['complete'])
            self.assertEqual(payload['count'], 1)


if __name__ == '__main__':
    unittest.main()
