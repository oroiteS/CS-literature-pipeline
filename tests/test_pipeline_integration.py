import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts import arxiv_search


class PipelineIntegrationTests(unittest.TestCase):
    def test_arxiv_preserves_journal_reference(self):
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
        <entry><id>https://arxiv.org/abs/2401.00001v1</id><title>Title</title>
        <arxiv:comment>Code available</arxiv:comment><arxiv:journal_ref>Journal 1 (2024)</arxiv:journal_ref>
        <arxiv:doi>10.1/example</arxiv:doi></entry></feed>'''
        paper = arxiv_search.parse(xml)[0]
        self.assertEqual(paper['journal_ref'], 'Journal 1 (2024)')
        self.assertEqual(paper['doi'], '10.1/example')

    def test_venue_change_invalidates_search_cache(self):
        paper = {'arxiv_id': '2401.00001', 'title': 'Title'}
        row = {'reason': 'venue_not_allowed', 'last_checked': date.today().isoformat(),
               'venue_fingerprint': 'old'}
        self.assertIsNone(arxiv_search.seen_recent(paper, {'2401.00001': row}, fingerprint='new'))
        self.assertEqual(arxiv_search.seen_recent(paper, {'2401.00001': row}, fingerprint='old'), row)
        row['reason'] = 'no_code'
        self.assertEqual(arxiv_search.seen_recent(paper, {'2401.00001': row}, fingerprint='new'), row)

    def test_doctor_forwarded_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / 'profile.yaml'
            venues = root / 'venues.yaml'
            profile.write_text('keywords: [NLP]\narxiv_categories: [cs.CL]\n', encoding='utf-8')
            venues.write_text('venues: [ACL, Nature]\n', encoding='utf-8')
            with patch.object(arxiv_search, 'PROFILE', profile), patch.object(arxiv_search, 'VENUES', venues), \
                 patch('sys.argv', ['arxiv_search.py', '--doctor']), contextlib.redirect_stdout(io.StringIO()) as out:
                arxiv_search.main()
            self.assertTrue(json.loads(out.getvalue())['configured'])


if __name__ == '__main__':
    unittest.main()
