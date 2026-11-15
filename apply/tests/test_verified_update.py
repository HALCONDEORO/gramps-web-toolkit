"""Readback and durable preimage of invented objects, no remote API."""
import copy,json,os
import pytest,requests
from fake_api import FakeGrampsWeb,Resp
from gwt_apply.client import Client,ApiError,Retryable

def setup(tmp_path):
    f=FakeGrampsWeb();f.add('events',{'handle':'event','type':'Birth','description':'Example'})
    c=Client('https://gw.example','u','pw',tree='tree-test',dry_run=False,session=f,log_path=str(tmp_path/'log.jsonl'));c.login()
    obj=c.get_live('Event','event');obj['_class']='Event';obj['type']='Baptism'
    return f,c,obj

def test_preimage_fsynced_before_put_and_get_after(tmp_path,monkeypatch):
    f,c,obj=setup(tmp_path);real=f.request;sync=[];fsync=os.fsync
    def synced(fd):sync.append(fd);return fsync(fd)
    monkeypatch.setattr(os,'fsync',synced)
    def request(method,url,**kw):
        if method=='PUT':
            assert sync
            records=[json.loads(x) for x in (tmp_path/'log.jsonl').read_text().splitlines()]
            assert records[-1]['event']=='update-preimage'
            assert records[-1]['before']['type']=='Birth' and records[-1]['intended']['type']=='Baptism'
        return real(method,url,**kw)
    f.request=request;c.update(obj)
    assert f.calls[-1]==('GET','/api/events/event') and len(f.writes())==1
    assert (tmp_path/'log.jsonl').stat().st_mode & 0o777 == 0o600
    assert json.loads((tmp_path/'log.jsonl').read_text().splitlines()[-1])['state']=='verified'

@pytest.mark.parametrize('landed',[True,False])
def test_lost_answer_reconciled_once_before_retry_is_allowed(tmp_path,landed):
    f,c,obj=setup(tmp_path);real=f.request
    def request(method,url,**kw):
        if method=='PUT':
            if landed:real(method,url,**kw)
            raise requests.Timeout('lost')
        return real(method,url,**kw)
    f.request=request
    with pytest.raises(Retryable):c.update(obj)
    records=[json.loads(x) for x in (tmp_path/'log.jsonl').read_text().splitlines()]
    assert records[-1]['event']=='update-reconciliation'
    assert records[-1]['state']==('landed' if landed else 'unchanged')
    assert len(f.writes())==(1 if landed else 0)

def test_unknown_divergent_state_stops_without_resend(tmp_path):
    f,c,obj=setup(tmp_path);real=f.request;puts=[]
    def request(method,url,**kw):
        if method=='PUT':
            puts.append(1);real(method,url,**kw);f.store['events']['event']['description']='external edit'
            raise requests.Timeout('lost')
        return real(method,url,**kw)
    f.request=request
    with pytest.raises(ApiError,match='no automatic retry'):c.update(obj)
    assert len(puts)==1 and f.store['events']['event']['description']=='external edit'

def test_success_with_unexpected_field_change_is_not_verified(tmp_path):
    f,c,obj=setup(tmp_path);real=f.request
    def request(method,url,**kw):
        out=real(method,url,**kw)
        if method=='PUT':f.store['events']['event']['description']='unexpected'
        return out
    f.request=request
    with pytest.raises(ApiError,match='readback differs'):c.update(obj)
    assert len(f.writes())==1

def test_preimage_storage_failure_prevents_put(tmp_path,monkeypatch):
    f,c,obj=setup(tmp_path)
    def fail(fd):raise OSError('fsync failed')
    monkeypatch.setattr(os,'fsync',fail)
    with pytest.raises(OSError):c.update(obj)
    assert f.writes()==[]

def test_snapshot_full_preimage_detects_same_change_stamp_external_edit(tmp_path):
    f,c,_=setup(tmp_path)
    c.use_snapshot({'tree':'tree-test','objects':{'Event':[copy.deepcopy(f.store['events']['event'])]}})
    obj=c.get('Event','event');obj['_class']='Event';obj['type']='Baptism'
    stamp=f.store['events']['event']['change'];f.store['events']['event']['description']='external same second'
    assert f.store['events']['event']['change']==stamp
    with pytest.raises(Retryable,match='changed since read'):c.update(obj)
    assert f.writes()==[]

def test_success_readback_unavailable_does_not_retry_put(tmp_path):
    f,c,obj=setup(tmp_path);real=f.request;sent=[]
    def request(method,url,**kw):
        if method=='PUT':sent.append(1)
        elif method=='GET' and sent:raise requests.Timeout('readback unavailable')
        return real(method,url,**kw)
    f.request=request
    with pytest.raises(ApiError,match='readback unavailable'):c.update(obj)
    assert len(sent)==1

def test_new_directory_chain_and_log_entry_fsynced_before_put(tmp_path,monkeypatch):
    import stat
    f,c,obj=setup(tmp_path);c._log.close();c._log=None;c._log_durable=False
    c._log_path=str(tmp_path/'new'/'nested'/'log.jsonl')
    real_sync=os.fsync;dirs=[];real=f.request
    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):dirs.append(os.readlink(f'/proc/self/fd/{fd}'))
        return real_sync(fd)
    monkeypatch.setattr(os,'fsync',sync)
    def request(method,url,**kw):
        if method=='PUT':
            assert str(tmp_path/'new'/'nested') in dirs and str(tmp_path/'new') in dirs
            assert str(tmp_path) in dirs and '/' in dirs
            assert dirs.count(str(tmp_path/'new'/'nested'))>=2  # path chain and new file entry
        return real(method,url,**kw)
    f.request=request;c.update(obj)


def test_directory_fsync_failure_prevents_put(tmp_path,monkeypatch):
    import stat
    f,c,obj=setup(tmp_path);c._log.close();c._log=None;c._log_durable=False;c._log_path=str(tmp_path/'nested'/'log.jsonl')
    real_sync=os.fsync
    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):raise OSError('directory fsync failed')
        return real_sync(fd)
    monkeypatch.setattr(os,'fsync',sync)
    with pytest.raises(OSError,match='directory fsync'):c.update(obj)
    assert f.writes()==[]
