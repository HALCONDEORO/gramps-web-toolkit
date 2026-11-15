"""Captured public locator shape, no live requests or private tree data.
Source: https://archives.touraine.fr/visualizer/api?naan=37621&arkName=lmdszj80tbk7&start=0&end=10&group=0
Captured 2026-11-15 by the tree reader; retained URL fields only.
"""
import json
from pathlib import Path
import pytest
from gwt_fetch import touraine

DATA = json.loads((Path(__file__).parent / 'fixtures/touraine-zero-based-urls.json').read_text())

class Response:
    status_code = 200
    headers = {'Content-Type': 'application/json'}
    def __init__(self, value): self.text = json.dumps(value)

class Client:
    def __init__(self): self.calls = []
    def get(self, url, *, params, allow_redirects):
        assert allow_redirects is False
        a,b = params['start'],params['end']; self.calls.append((a,b))
        if b >= len(DATA):
            r = Response(touraine.END_OF_REGISTER); r.status_code = 400; return r
        return Response(DATA[a:b+1] if a == 0 else {str(i): DATA[i] for i in range(a,b+1)})

def test_captured_first_image_and_all_eleven_ordinals():
    c = Client(); pages = touraine.list_pages(c, 'lmdszj80tbk7')
    assert pages == {i+1:r['url'] for i,r in enumerate(DATA)}
    assert pages[1].endswith('90cc8279-e878-4e2f-b48c-fea20955e3d4')
    assert pages[2].endswith('733bca52-8a3f-46d5-8dd6-983692908500')
    assert c.calls[0] == (0,99)

def test_captured_second_image_dict_key_is_api_index_not_view_number():
    assert touraine._batch(Client(), 'lmdszj80tbk7', 1, 1) == {1:DATA[1]['url']}

@pytest.mark.parametrize('value', [{}, {'1':DATA[1]}, {'0':DATA[0],'2':DATA[2]}, [DATA[0]], {'0':{}}, {'x':DATA[0]}])
def test_partial_shifted_or_bad_rows_fail_closed(value):
    class Bad:
        def get(self,*a,**kw):return Response(value)
    with pytest.raises(touraine.Blocked):touraine._batch(Bad(),'lmdszj80tbk7',0,1)
