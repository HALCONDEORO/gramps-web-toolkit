import copy
import pytest
from gwt_fetch import geo_fr, cli


def row(name='Exemple-sur-Loire', code='37001', dep='37', xy=None):
    return {'nom':name,'code':code,'codeDepartement':dep,'centre':{'type':'Point','coordinates':xy or [0.8,47.4]}}

class Client:
    def __init__(self, data):self.data=data;self.calls=[]
    def json(self,url,**kw):self.calls.append((url,kw));return self.data


def test_exact_coordinates_provenance_and_no_mutation():
    data=[row()];before=copy.deepcopy(data);c=Client(data);got=geo_fr.geocode(c,'Exemple-sur-Loire')
    assert data==before and got[0]['lat']=='47.400000' and got[0]['lng']=='0.800000'
    assert got[0]['status']=='matched' and got[0]['requires_human_review'] is True
    assert got[0]['current_commune_only'] is True and got[0]['source']==geo_fr.API+'/37001'
    assert c.calls[0][1]['params']['nom']=='Exemple-sur-Loire'


def test_homonyms_no_population_winner_and_department_filter():
    c=Client([row(),row(code='41001',dep='41')]);assert all(r['status']=='ambiguous' for r in geo_fr.geocode(c,'Exemple-sur-Loire'))
    got=geo_fr.geocode(c,'Exemple-sur-Loire',department='41');assert len(got)==1 and got[0]['insee_code']=='41001'
    assert c.calls[-1][1]['params']['codeDepartement']=='41'


def test_fuzzy_not_selected_empty_and_truncation():
    assert geo_fr.geocode(Client([]),'Absent')==[]
    got=geo_fr.geocode(Client([row(),row(code='41001')]),'Different',limit=1)
    assert got[0]['status']=='truncated-review' and got[0]['candidate_count']==2
    assert geo_fr.geocode(Client([row()]),'Different')[0]['status']=='ambiguous'


def test_fold_order_and_exact_code():
    assert geo_fr.fold('Éxemple-sur Loire')==geo_fr.fold('exemple sur-loire')
    c=Client(row());got=geo_fr.geocode(c,'Exemple-sur-Loire',code='37001');assert got[0]['insee_code']=='37001'
    assert c.calls[0][0].endswith('/37001')
    a=[row(code='41001'),row()];assert geo_fr.geocode(Client(a),'X')==geo_fr.geocode(Client(list(reversed(a))),'X')

@pytest.mark.parametrize('value',[True,float('nan'),float('inf'),181,'x'])
def test_invalid_coordinates(value):
    with pytest.raises(ValueError):geo_fr.geocode(Client([row(xy=[value,47])]),'X')

@pytest.mark.parametrize('kwargs',[{'limit':0},{'limit':31},{'limit':True},{'department':'37/evil'},{'code':'bad'}])
def test_invalid_parameters_no_network(kwargs):
    c=Client([])
    with pytest.raises(ValueError):geo_fr.geocode(c,'X',**kwargs)
    assert not c.calls

@pytest.mark.parametrize('data',[{},[{}],[{'nom':'X\tY'}],None])
def test_bad_payload(data):
    with pytest.raises(ValueError):geo_fr.geocode(Client(data),'X')

def test_cli_and_output(capsys,monkeypatch):
    a=cli.parser().parse_args(['geo-fr','Exemple-sur-Loire','--department','37'])
    assert a.cmd=='geo-fr'
    assert geo_fr.run(a,Client([row()]))==0
    assert '47.400000\t0.800000' in capsys.readouterr().out
    assert geo_fr.run(a,Client([]))==1
