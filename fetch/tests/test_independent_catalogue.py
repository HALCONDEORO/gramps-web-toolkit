"""Offline synthetic tests; run with PYTHONPATH=fetch python3 -m unittest discover -s DIR."""
import unittest
from gwt_fetch import touraine_catalogue as c

class Resp:
    headers={'Content-Type':'text/html'}
    def __init__(self,text,status=200):self.text=text;self.status_code=status
class Client:
    def __init__(self,pages):self.pages=iter(pages)
    def get(self,*a,**k):
        try:
            return next(self.pages)
        except StopIteration as exc:
            raise AssertionError("unexpected extra request") from exc
def row(ark='invent001',date='1700',place='Exampleville'):
    return f'<tr class="result record"><td><a href="/ark:/37621/{ark}"></a><h2><span>Invented register</span></h2></td>'+''.join(f'<td><span class="label">{label}</span>{text}</td>' for label,text in [('Cote','6NUM/'+ark),('Date',date),('Lieu',place),('Type de document','etat civil')])+'</tr>'
def table(*rows):return '<table class="results-table">'+''.join(rows)+'</table>'
BASE=table(row(),row('invent002','1704'))
def collect(last):return c.collect(Client([Resp(BASE),last]),'Exampleville')
class IndependentCatalogueTests(unittest.TestCase):
    def test_partial_unrecognized_row_does_not_prove_end(self):
        result=collect(Resp(table('<tr><td>record not parsed</td></tr>')))
        self.assertFalse(result['complete']);self.assertIsNone(c.year_coverage(result)['uncovered'])
    def test_next_rel_token_does_not_prove_end(self):
        result=collect(Resp(table()+'<a rel="nofollow next" href="?page=3">Next</a>'))
        self.assertFalse(result['complete']);self.assertIsNone(c.year_coverage(result)['uncovered'])
    def test_challenge_does_not_prove_end(self):
        result=collect(Resp('<html><h1>Verify you are human</h1>'+table()+'</html>'))
        self.assertFalse(result['complete']);self.assertIsNone(c.year_coverage(result)['uncovered'])
    def test_different_commune_withholds_commune_gap_claim(self):
        result=c.collect(Client([Resp(table(row(),row('invent002','1704','Elsewhere'))),Resp(table())]),'Exampleville')
        self.assertIsNone(c.year_coverage(result)['uncovered'])
    def test_refusals_do_not_prove_end(self):
        for status in [403,429,500,302]:
            with self.subTest(status=status):
                result=collect(Resp(table(),status))
                self.assertFalse(result['complete']);self.assertIsNone(c.year_coverage(result)['uncovered'])
    # No captured terminal contract exists. The former positive control used a
    # synthetic empty table, which can also occur on denial/challenge pages.
    def assert_unknown_terminal(self, html):
        result = collect(Resp(html))
        self.assertFalse(result['complete'])
        self.assertIn('UNKNOWN', result['reason'])
        self.assertIsNone(c.year_coverage(result)['uncovered'])
        self.assertEqual(len(result['rows']), 2)
        self.assertEqual([r['source_row'] for r in result['rows']], [1, 2])
        self.assertTrue(all(r['source_url'] and r['record_url'] for r in result['rows']))

    def test_unmarked_empty_table_is_unknown(self):
        self.assert_unknown_terminal(table())

    def test_access_denied_title_empty_table_is_unknown(self):
        self.assert_unknown_terminal('<html><head><title>Access denied</title></head><body>' + table() + '</body></html>')

    def test_non_english_human_challenge_empty_table_is_unknown(self):
        self.assert_unknown_terminal('<html><body><h1>Vérifiez que vous êtes humain</h1>' + table() + '</body></html>')

    def test_recaptcha_widget_empty_table_is_unknown(self):
        self.assert_unknown_terminal('<html><body><div class="g-recaptcha" data-sitekey="invented"></div>' + table() + '</body></html>')
