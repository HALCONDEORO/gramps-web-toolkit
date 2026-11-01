import pytest
from gwt_fetch.index_locator import IndexLocator, ImageMapping, locate
URL='https://example.invalid/index'
def locator(**changes):
 values=dict(book_id='BookA',reference_kind='folio',number=12,side='recto',source_url=URL,checked_on='2026-11-01')
 values.update(changes)
 return IndexLocator(**values)
def mapping(**changes):
 values=dict(book_id='BookA',reference_kind='folio',number=12,side='recto',image_number=29,index_base='one',source_url='https://example.invalid/crosswalk',checked_on='2026-11-01')
 values.update(changes)
 return ImageMapping(**values)
def test_index_is_a_clue_not_an_examined_act():
 out=locate(locator())
 assert out['image_target'] is None
 assert out['state']=='mapping_needed'
 assert out['act_examined'] is False
 assert out['reference']['number']==12
 assert out['reference']['kind']=='folio'
def test_explicit_mapping_returns_exact_number_without_arithmetic():
 out=locate(locator(),mapping())
 assert out['image_target']=={'number':29,'index_base':'one'}
 assert out['mapping_source']['url'].endswith('crosswalk')
 assert out['act_examined'] is False
@pytest.mark.parametrize('changes',[dict(book_id='BookB'),dict(reference_kind='page',side=None),dict(number=13),dict(side='verso')])
def test_no_cross_book_page_folio_or_face_inference(changes):
 with pytest.raises(ValueError):locate(locator(),mapping(**changes))
def test_zero_based_explicit_mapping_preserved():
 out=locate(locator(),mapping(image_number=0,index_base='zero'))
 assert out['image_target']=={'number':0,'index_base':'zero'}
@pytest.mark.parametrize('changes',[dict(number=True),dict(number=0),dict(side=None),dict(reference_kind='image'),dict(source_url='https://example.invalid:bad/x'),dict(checked_on='2026-99-99')])
def test_invalid_locator_refused(changes):
 with pytest.raises(ValueError):locator(**changes)
@pytest.mark.parametrize('changes',[dict(index_base=None),dict(image_number=True),dict(image_number=0,index_base='one'),dict(side='verso',reference_kind='page')])
def test_invalid_mapping_refused(changes):
 with pytest.raises(ValueError):mapping(**changes)
def test_page_has_no_face_and_is_not_folio():
 p=locator(reference_kind='page',side=None)
 assert locate(p)['reference']['side'] is None

def test_source_and_output_are_independent():
 out=locate(locator(),mapping());out['reference']['number']=99
 assert locate(locator(),mapping())['reference']['number']==12


def test_cli_uses_no_network_and_rejects_duplicate_json(tmp_path, capsys, monkeypatch):
    import json
    from gwt_fetch.cli import main
    import gwt_fetch.net
    monkeypatch.setattr(gwt_fetch.net, 'Client', lambda **kwargs: pytest.fail('no network client'))
    path = tmp_path / 'reference.json'
    path.write_text(json.dumps(locator().__dict__))
    assert main(['index-locate', str(path)]) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'mapping_needed'
    path.write_text('{"book_id":"BookA","book_id":"BookB"}')
    assert main(['index-locate', str(path)]) == 2
    assert capsys.readouterr().out == ''
