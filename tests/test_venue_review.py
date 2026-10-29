import argparse
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import dedup, venue_review


class VenueReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.venues = root / 'venues.yaml'
        self.venues.write_text('venues:\n  - name: ICML\n    aliases: [ICML, I-C-M-L]\n', encoding='utf-8')
        self.seen, self.pushed = root / 'seen.csv', root / 'pushed.csv'
        for obj, name, value in ((venue_review, 'VENUES', self.venues),
                                 (dedup, 'SEEN', self.seen), (dedup, 'PUSHED', self.pushed)):
            p = patch.object(obj, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.args = argparse.Namespace(title='A paper', arxiv_id='', doi='', reason='venue_not_allowed')

    def call(self, func, args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = func(args)
        return code, json.loads(out.getvalue())

    def test_semantic_fingerprint(self):
        original = venue_review.venue_fingerprint()
        self.venues.write_text('# comment\nvenues:\n  - aliases: [I-C-M-L, ICML]\n    name: ICML\n', encoding='utf-8')
        self.assertEqual(original, venue_review.venue_fingerprint())
        self.venues.write_text('venues: [{name: NeurIPS}]\n', encoding='utf-8')
        self.assertNotEqual(original, venue_review.venue_fingerprint())

    def test_reject_check_and_change(self):
        self.call(dedup.cmd_reject, self.args)
        self.assertEqual(self.call(dedup.cmd_check, self.args)[0], 2)
        row = dedup.read_rows(self.seen)[0]
        self.assertFalse(venue_review.venue_recheck_required(row))
        self.venues.write_text('venues: [{name: NeurIPS}]\n', encoding='utf-8')
        self.assertTrue(venue_review.venue_recheck_required(row))
        code, result = self.call(dedup.cmd_check, self.args)
        self.assertEqual((code, result['action']), (3, 're-evaluate'))
        self.call(dedup.cmd_reject, self.args)
        self.assertEqual(self.call(dedup.cmd_check, self.args)[0], 2)

    def test_legacy_and_other_reason(self):
        with self.seen.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=dedup.SEEN_COLS[:-1])
            w.writeheader()
            from datetime import date
            w.writerow(dict(title='A paper', title_norm='a paper', reason='venue_not_allowed', last_checked=date.today().isoformat()))
        self.assertEqual(self.call(dedup.cmd_check, self.args)[0], 3)
        self.args.reason = 'no_code'
        self.call(dedup.cmd_reject, self.args)
        self.assertEqual(self.call(dedup.cmd_check, self.args)[0], 2)
        self.venues.write_text('venues: []\n', encoding='utf-8')
        self.assertEqual(self.call(dedup.cmd_check, self.args)[0], 2)

    def test_push_venue_year_alias(self):
        import sys
        with patch.object(sys, 'argv', ['dedup.py', 'push', '--title', 'A paper', '--venue-year', '2025']):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(dedup.main(), 0)
        self.assertEqual(dedup.read_rows(self.pushed)[0]['venue_year'], '2025')


if __name__ == '__main__':
    unittest.main()
