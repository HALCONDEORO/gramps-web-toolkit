"""Invented unilateral family, single forward write and recoverable compensation on revert."""
import copy,gzip,json
import pytest
from test_ops_new import Fake,run,state,_client
from gwt_apply import revert
from gwt_apply.ops import validate

class FaithfulFamilyFake(Fake):
    """API3.25.1 removal and recommit, not the legacy additions-only shared fake.

    Source: https://raw.githubusercontent.com/gramps-project/gramps-webapi/v3.25.1/gramps_webapi/api/resources/util.py
    update_family_update_refs. No real API calls.
    """
    def _family_refs(self, old: dict | None, new: dict):
        """What the real server does on a family write: keep the persons' family links in step."""
        people = self.store["people"]
        h = new["handle"]
        def members(f):
            return ({f.get("father_handle"), f.get("mother_handle")} - {None},
                    {r["ref"] for r in f.get("child_ref_list") or []})
        old_p, old_c = members(old or {})
        new_p, new_c = members(new)
        for handles, lst in ((old_p - new_p, "family_list"), (old_c - new_c, "parent_family_list")):
            for person in handles:
                people[person][lst] = [x for x in people[person][lst] if x != h]
                people[person]["change"] = self.tick()
        for handles, lst in ((new_p - old_p, "family_list"), (new_c - old_c, "parent_family_list")):
            for person in handles:
                if h not in people[person][lst]:
                    people[person][lst].append(h)
                # API3.25.1 added set members recommit even if the Person already listed this family.
                people[person]["change"] = self.tick()


@pytest.fixture
def sample():
    f=FaithfulFamilyFake();f.add('sources',{'handle':'src','title':'Example register'})
    f.add('citations',{'handle':'cit','source_handle':'src','page':'folio 8'})
    f.add('people',{'handle':'kid','parent_family_list':['fam'],'primary_name':{'first_name':'Example'}})
    f.add('families',{'handle':'fam','child_ref_list':[]})
    op={'op':'repair_child_backlink','key':'repair-1','ruling':'reviewed-original-1','family':f.store['families']['fam']['gramps_id'],
        'child':f.store['people']['kid']['gramps_id'],'citation':f.store['citations']['cit']['gramps_id'],'frel':'Birth','mrel':'Unknown'}
    return f,op

def test_single_put_with_existing_citation_and_no_person_put(sample,tmp_path):
    f,op=sample;assert validate([op])==[]
    before=state(f);assert run(f,[op],dry_run=True)[0].status=='done';assert state(f)==before
    log=str(tmp_path/'log.jsonl');assert run(f,[op],log=log)[0].status=='done'
    assert f.writes()==[('PUT','/api/families/fam')]
    ref=f.store['families']['fam']['child_ref_list'][0]
    assert ref['citation_list']==['cit'] and ref['frel']=='Birth' and ref['mrel']=='Unknown'
    assert f.store['people']['kid']['parent_family_list']==['fam']
    assert run(f,[op],verify=True)[0].status=='skip'
    assert len(f.writes())==1
    assert revert.read_log(log)[0]['preserve_parent_link']=={'child':'kid','family':'fam'}

def test_revert_preserves_older_person_link_after_server_removes_it(sample,tmp_path):
    f,op=sample;before=state(f);pre={ (cls,h):copy.deepcopy(o) for cls,ns in __import__('gwt_apply.client',fromlist=['NAMESPACE']).NAMESPACE.items() for h,o in f.store[ns].items() }
    log=str(tmp_path/'log.jsonl');run(f,[op],log=log);c=_client(f)
    steps=revert.plan(revert.read_log(log),pre,c.get_live)
    assert any(s['action']=='preserve-parent-link' for s in steps)
    assert revert.execute(c,steps,pre,retry_wait=0)==[]
    assert state(f)==before
    assert f.writes()==[('PUT','/api/families/fam'),('PUT','/api/families/fam'),('PUT','/api/people/kid')]

@pytest.mark.parametrize('bad',['missing-person-side','missing-citation','existing-other-ref'])
def test_preconditions_conflict_without_write(sample,bad):
    f,op=sample
    if bad=='missing-person-side':f.store['people']['kid']['parent_family_list']=[]
    elif bad=='missing-citation':op['citation']='C9999'
    else:f.store['families']['fam']['child_ref_list']=[{'ref':'kid','frel':'Adopted','mrel':'Birth','citation_list':[]}]
    assert run(f,[op])[0].status=='conflict' and f.writes()==[]

def test_concurrent_person_edit_prevents_family_reversal(sample,tmp_path):
    f,op=sample;pre={(cls,h):copy.deepcopy(o) for cls,ns in __import__('gwt_apply.client',fromlist=['NAMESPACE']).NAMESPACE.items() for h,o in f.store[ns].items()}
    log=str(tmp_path/'log.jsonl');run(f,[op],log=log);f.store['people']['kid']['private']=True;c=_client(f)
    steps=revert.plan(revert.read_log(log),pre,c.get_live)
    assert any(s['action']=='manual' for s in steps)
    assert not any(s['action']=='restore' and s['cls']=='Family' for s in steps)
    assert len(f.writes())==1

def test_person_drift_after_plan_stops_before_family_write(sample,tmp_path):
    f,op=sample;pre={(cls,h):copy.deepcopy(o) for cls,ns in __import__('gwt_apply.client',fromlist=['NAMESPACE']).NAMESPACE.items() for h,o in f.store[ns].items()}
    log=str(tmp_path/'log.jsonl');run(f,[op],log=log);c=_client(f)
    steps=revert.plan(revert.read_log(log),pre,c.get_live)
    f.store['people']['kid']['private']=True
    revert.execute(c,steps,pre,retry_wait=0)
    assert len(f.writes())==1 and f.store['people']['kid']['parent_family_list']==['fam']
    assert f.store['families']['fam']['child_ref_list']

def test_recommit_stamp_and_removal_in_shared_double(sample):
    f,op=sample;stamp=f.store['people']['kid']['change'];run(f,[op])
    assert f.store['people']['kid']['change']>stamp
    c=_client(f);family=c.get_live('Family','fam');family['_class']='Family';family['child_ref_list']=[];c.update(family)
    assert f.store['people']['kid']['parent_family_list']==[]

def test_second_compensation_failure_is_partial_no_blind_retry(sample,tmp_path):
    from gwt_apply.client import ApiError,Retryable
    f,op=sample;pre={(cls,h):copy.deepcopy(o) for cls,ns in __import__('gwt_apply.client',fromlist=['NAMESPACE']).NAMESPACE.items() for h,o in f.store[ns].items()}
    log=str(tmp_path/'log.jsonl');run(f,[op],log=log);c=_client(f);steps=revert.plan(revert.read_log(log),pre,c.get_live)
    real=c.update;attempts=[]
    def update(body):
        if body['_class']=='Person':attempts.append(body);raise Retryable('dropped answer')
        return real(body)
    c.update=update
    with pytest.raises(ApiError,match='partial or unknown'):revert.execute(c,steps,pre,retry_wait=0)
    assert len(attempts)==1 and f.store['families']['fam']['child_ref_list']==[]
    assert f.store['people']['kid']['parent_family_list']==[]
    replay=revert.plan(revert.read_log(log),pre,c.get_live)
    assert any(s['action']=='manual' and 'partial compensation' in s['why'] for s in replay)
    assert not any(s['action']=='preserve-parent-link' for s in replay)


def test_unchanged_family_member_is_not_recommitted(sample):
    f,op=sample;run(f,[op]);stamp=f.store['people']['kid']['change']
    c=_client(f);family=c.get_live('Family','fam');family['_class']='Family';family['private']=True;c.update(family)
    assert f.store['people']['kid']['change']==stamp
