"""Crossref 搜索的离线测试；不请求网络。"""
import contextlib
from datetime import date
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from scripts import journal_search as js

START = date(2024, 3, 1)
END = date(2024, 3, 31)


def work(name='Journal of Testing', kind='journal-article', parts=None, **kwargs):
    result = {'container-title': [name], 'type': kind, 'DOI': '10.1/example',
              'title': ['Example'], 'URL': 'https://doi.org/10.1/example'}
    if parts is not None:
        result['published-online'] = {'date-parts': [parts]}
    result.update(kwargs)
    return result


def page(items, cursor='next'):
    return {'status': 'ok', 'message': {'items': items, 'next-cursor': cursor}}


class JournalSearchTests(unittest.TestCase):
    def test_exact_name_and_type(self):
        items = [work(' JOURNAL   OF TESTING ', parts=[2024, 3, 1]),
                 work('Journal of Testing Methods', parts=[2024, 3, 2]),
                 work('Journal of Testing', kind='proceedings-article', parts=[2024, 3, 2]),
                 work('Journal of Testing', kind='posted-content', parts=[2024, 3, 2]),
                 work('Journal of Testing!', parts=[2024, 3, 2])]
        results, report = js.search('Journal of Testing', START, END, 1, rows=10,
                                    fetch=lambda *args: page(items))
        self.assertEqual(len(results), 1)
        self.assertTrue(report['complete'])
        self.assertEqual(results[0]['verification'], 'pending_official_verification')
        self.assertEqual(results[0]['doi'], '10.1/example')
        self.assertEqual(results[0]['title'], 'Example')

    def test_dates_are_not_conflated(self):
        items = [work(parts=[2024, 3, 31]), work(parts=[2024, 4, 1]),
                 work(parts=[2024, 3]), work(parts=[2024]), work(parts=[2023]),
                 work(published={'date-parts': [[2024, 3, 4]]}),
                 work(parts=[2024, 2, 30]), work(parts=[2024, 2])]
        results, _ = js.search('Journal of Testing', START, END, 1, rows=20,
                               fetch=lambda *args: page(items))
        self.assertEqual([x['date_status'] for x in results],
                         ['in_range', 'in_range', 'partial_overlap', 'unknown', 'unknown'])
        self.assertEqual([x['first_online_date'] for x in results],
                         ['2024-03-31', '2024-03', '2024', None, None])
        self.assertEqual(results[1]['first_online_date_precision'], 'month')

    def test_cursor_pagination_and_budget(self):
        calls = []
        def get(journal, cursor, rows):
            calls.append((journal, cursor, rows))
            return page([work(parts=[2024, 3, 2])] * 2 if cursor == '*' else [work()], 'cursor2')
        results, report = js.search('Journal of Testing', START, END, 2, rows=2, fetch=get)
        self.assertEqual([x[1] for x in calls], ['*', 'cursor2'])
        self.assertEqual(len(results), 3)
        self.assertTrue(report['complete'])
        self.assertIsNone(report['next_cursor'])
        _, report = js.search('Journal of Testing', START, END, 1, rows=2, fetch=get)
        self.assertTrue(report['truncated'])
        self.assertFalse(report['complete'])
        self.assertEqual(report['next_cursor'], 'cursor2')
        resumed, resumed_report = js.search('Journal of Testing', START, END, 1, rows=2,
                                            fetch=get, cursor=report['next_cursor'])
        self.assertEqual(calls[-1][1], 'cursor2')
        self.assertEqual(len(resumed), 1)
        self.assertTrue(resumed_report['complete'])
        self.assertIsNone(resumed_report['next_cursor'])

    def test_partial_result_failure_and_invalid_cursor(self):
        responses = iter([page([work()], 'next'), OSError('offline')])
        def get(*args):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response
        results, report = js.search('Journal of Testing', START, END, 2, rows=1, fetch=get)
        self.assertEqual(len(results), 1)
        self.assertEqual(report['pages_fetched'], 1)
        self.assertTrue(report['failed'])
        self.assertIn('offline', report['error'])
        self.assertEqual(report['next_cursor'], 'next')
        _, failed = js.search('Journal of Testing', START, END, 1, rows=1,
                              fetch=lambda *args: (_ for _ in ()).throw(OSError('expired')),
                              cursor='resume')
        self.assertEqual(failed['next_cursor'], 'resume')
        self.assertIn('过期', failed['cursor_note'])
        for bad in [page([work()], '*'), {'status': 'ok', 'message': {'items': 'bad'}}, {}]:
            with self.subTest(bad=bad):
                _, report = js.search('Journal of Testing', START, END, 1, rows=1,
                                      fetch=lambda *args: bad)
                self.assertTrue(report['failed'])

    def test_fetch_request_encodes_cursor_without_date_filter(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps(page([])).encode()
        with patch.object(js, 'urlopen', return_value=Response()) as opened:
            js.fetch_page('Nature & Science', 'a+/=', 4)
        request = opened.call_args.args[0]
        params = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(params['query.container-title'], ['Nature & Science'])
        self.assertEqual(params['cursor'], ['a+/='])
        self.assertNotIn('filter', params)

    def test_cli_partial_json_and_exit_codes(self):
        for report, code in [({'failed': True, 'truncated': False}, 3),
                             ({'failed': False, 'truncated': True}, 4),
                             ({'failed': False, 'truncated': False}, 0)]:
            output = io.StringIO()
            with patch.object(js, 'search', return_value=([{'title': 'partial'}], report)), contextlib.redirect_stdout(output):
                result = js.main(['--journal', 'X', '--start', '2024-01-01', '--end', '2024-12-31', '--max-pages', '1'])
            self.assertEqual(result, code)
            self.assertEqual(json.loads(output.getvalue())['count'], 1)

    def test_cli_cursor_forwarded(self):
        output = io.StringIO()
        with patch.object(js, 'search', return_value=([], {'failed': False, 'truncated': False})) as search, contextlib.redirect_stdout(output):
            js.main(['--journal', 'X', '--start', '2024-01-01', '--end', '2024-12-31', '--max-pages', '1', '--cursor', 'a+/='])
        self.assertEqual(search.call_args.kwargs['cursor'], 'a+/=')
        with self.assertRaises(ValueError):
            js.search('X', START, END, 1, cursor=' ')

    def test_validation(self):
        for journal, start, end, budget in [('', START, END, 1), ('X', END, START, 1), ('X', START, END, 0)]:
            with self.subTest(journal=journal, budget=budget), self.assertRaises(ValueError):
                js.search(journal, start, end, budget)


if __name__ == '__main__':
    unittest.main()
