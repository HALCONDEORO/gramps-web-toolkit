import copy
import unittest
import gwt_report as R
import gwt_pedigree_reports as P
from test_occupations import tree


class PedigreeReports(unittest.TestCase):
    def test_completeness_slots_not_unknown_parent(self):
        T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'}
        r=P.completeness(T,'child',2)
        self.assertEqual((r['rows'][0]['theoretical_slots'],r['rows'][0]['occupied_slots'],r['rows'][0]['unique_people']),(2,1,1))
        T['people']['p']['primary_name']['first_name']='padre desconocido'
        self.assertEqual(P.completeness(T,'child',2)['rows'][0]['occupied_slots'],0)

    def test_no_source_living_and_private_not_discovered(self):
        for change in ['unsourced','living','private']:
            T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'}
            if change=='unsourced':T['citations']={}
            if change=='living':T['events']['d']['type']='Birth'
            if change=='private':T['people']['p']['private']=True
            self.assertEqual(P.completeness(T,'child',1)['rows'][0]['occupied_slots'],0)

    def test_implex_positions_and_unique_people(self):
        T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'}
        T['families']['f']['mother_handle']='p'
        r=P.completeness(T,'child',1)['rows'][0]
        self.assertEqual((r['occupied_slots'],r['unique_people'],r['repeated_slots']),(2,1,1))
        self.assertEqual(r['people'][0]['slots'],[2,3])

    def test_conflict_ambiguity_cycle_and_adoption_excluded(self):
        for mode in ['conflict','ambiguous','cycle','adoption']:
            T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'}
            if mode=='conflict':T['tags']['t']={'name':'hipótesis en conflicto'};T['families']['f']['tag_list']=['t']
            if mode=='ambiguous':
                T['people']['x']={'handle':'x'};T['families']['f2']={'father_handle':'x','child_ref_list':[{'ref':'child','frel':'Birth'}]};T['people']['child']['parent_family_list'].append('f2')
            if mode=='cycle':T['families']['f']['father_handle']='child'
            if mode=='adoption':T['families']['f']['child_ref_list'][0]['frel']='Adopted'
            r=P.completeness(T,'child',1)
            self.assertEqual(r['rows'][0]['occupied_slots'],0)
            self.assertTrue(r['excluded_links'])

    def test_evidence_readings_not_inferred_from_notes(self):
        T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'}
        T['notes']['n']={'text':'two readings, confirmed'};T['people']['p']['note_list']=['n']
        self.assertEqual(P.evidence_fan(T,'child',[])['rows'][0]['stage'],'unclassified-source')
        base=dict(person='I1',citation='C1',kind='act',literal='Literal invented record',literal_url='https://example.org/act',readings=['reader-A'],conflict=False)
        for readings,stage in [([], 'unclassified-source'),(['A'],'one-reading'),(['A','B'],'two-readings')]:
            row={**base,'readings':readings}
            self.assertEqual(P.evidence_fan(T,'child',[row])['rows'][0]['stage'],stage)
        self.assertEqual(P.evidence_fan(T,'child',[{**base,'conflict':True}])['rows'][0]['stage'],'contradiction')
        self.assertFalse(P.evidence_fan(T,'child',[{**base,'conflict':True}])['rows'][0]['parentage_verified'])
        with self.assertRaises(R.BadInput):P.evidence_fan(T,'child',[{**base,'citation':'someone-elses-citation'}])

    def test_unknown_generation_depth_no_root(self):
        with self.assertRaises(R.BadInput):P.ancestry(tree(),None)
        for depth in [-1,13]:
            with self.assertRaises(R.BadInput):P.ancestry(tree(),'child',depth)


class AdversarialPrivacy(unittest.TestCase):
    def test_private_citation_and_source_not_leaked_by_default(self):
        import gwt_occupations as O
        for kind,key in [('citations','c'),('sources','s')]:
            T=tree();T[kind][key]['private']=True
            self.assertEqual(O.report(T,root='child')['rows'],[])

    def test_manifest_only_accepts_unique_reader_records(self):
        import tempfile,json
        from pathlib import Path
        row=dict(person='I1',citation='C1',kind='act',literal='Invented literal',literal_url='https://example.org/record',readings=['A','A'],conflict=False)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'evidence.json';p.write_text(json.dumps([row]))
            with self.assertRaises(R.BadInput):P.evidence_records(p)

class IndependentReviewRegressions(unittest.TestCase):
    def test_private_family_childref_and_place(self):
        import gwt_occupations as O
        for target in ['family','childref']:
            T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'}
            if target=='family':T['families']['f']['private']=True
            else:T['families']['f']['child_ref_list'][0]['private']=True
            self.assertEqual(O.report(T,root='child')['rows'],[])
            self.assertEqual(P.completeness(T,'child',1)['rows'][0]['occupied_slots'],0)
            self.assertEqual(P.evidence_fan(T,'child',[])['rows'],[])
        T=tree();T['places']['l']['private']=True
        r=O.report(T,root='child')['rows'][0]
        self.assertIsNone(r['place']);self.assertIsNone(r['place_id'])

    def test_conflicting_parent_and_family_remain_visible_not_discoveries(self):
        for target in ['person','family']:
            T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'};T['tags']['t']={'name':'contradiction'}
            if target=='person':T['people']['p']['tag_list']=['t']
            else:T['families']['f']['tag_list']=['t']
            rows=P.evidence_fan(T,'child',[])['rows']
            self.assertEqual(rows[0]['stage'],'contradiction')
            self.assertFalse(rows[0]['parentage_verified'])
            self.assertEqual(P.completeness(T,'child',1)['rows'][0]['occupied_slots'],0)

    def test_no_manifest_never_claims_discovery(self):
        T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'}
        r=P.evidence_fan(T,'child',[])['rows'][0]
        self.assertNotIn('counted_as_discovery',r)
        self.assertFalse(r['parentage_verified'])
        self.assertTrue(r['has_attached_source'])

    def test_generation_boundary_reports_cutoff(self):
        T=tree();T['people']['p']['parent_family_list']=['up'];T['people']['grand']={'handle':'grand'}
        T['families']['up']={'father_handle':'grand','child_ref_list':[{'ref':'p','frel':'Birth'}]}
        slots,issues=P.ancestry(T,'child',1)
        self.assertTrue(any(x['reason']=='generation-limit' for x in issues))

class ReviewV5Regressions(unittest.TestCase):
    def test_conflicting_competitor_blocks_whole_slot(self):
        import gwt_occupations as O
        for target in ['parent','family']:
            T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'}
            T['people']['alt']=copy.deepcopy(T['people']['p']);T['people']['alt'].update(handle='alt',gramps_id='ALT')
            T['families']['other']={'father_handle':'alt','child_ref_list':[{'ref':'child','frel':'Birth'}]}
            T['people']['child']['parent_family_list'].append('other');T['tags']['t']={'name':'contradiction'}
            (T['people']['alt'] if target=='parent' else T['families']['other'])['tag_list']=['t']
            self.assertEqual(P.completeness(T,'child',1)['rows'][0]['occupied_slots'],0)
            self.assertEqual(O.report(T,root='child')['rows'],[])
            row=P.evidence_fan(T,'child',[])['rows'][0]
            self.assertEqual(row['slots'],[2]);self.assertEqual(row['generations'],[1])

    def test_death_privacy_and_conflict_fail_closed(self):
        import gwt_occupations as O
        for target in ['event-private','ref-private','event-conflict','ref-conflict']:
            T=tree();T['people']['p']['primary_name']={'first_name':'Invented worker'};T['tags']['t']={'name':'contradiction'}
            obj=T['events']['d'] if target.startswith('event') else T['people']['p']['event_ref_list'][0]
            if target.endswith('private'):obj['private']=True
            else:obj['tag_list']=['t']
            self.assertEqual(O.report(T,root='child')['rows'],[])
            self.assertEqual(P.completeness(T,'child',1)['rows'][0]['occupied_slots'],0)
            self.assertEqual(P.evidence_fan(T,'child',[])['rows'],[])

    def test_no_living_candidate_id_in_output(self):
        T=tree();T['events']['d']['type']='Birth';T['tags']['t']={'name':'contradiction'};T['people']['p']['tag_list']=['t']
        import json
        result=P.evidence_fan(T,'child',[])
        self.assertNotIn('"candidate": "p"',json.dumps(result))

class AdoptedIndependentRole(unittest.TestCase):
    def test_birth_parent_survives_separate_adoptive_links(self):
        import gwt_occupations as O
        for target in ['ordinary','private-person','private-family','private-childref','conflict-person','conflict-family']:
            with self.subTest(target=target):
                T=tree();T['people']['p']['primary_name']={'first_name':'Birth father'}
                T['people']['adopt']=copy.deepcopy(T['people']['p']);T['people']['adopt'].update(handle='adopt',gramps_id='I3')
                T['families']['af']={'father_handle':'adopt','child_ref_list':[{'ref':'child','frel':'Adopted'}]}
                T['people']['child']['parent_family_list'].append('af');T['tags']['t']={'name':'contradiction'}
                if target.startswith('private'):
                    obj={'private-person':T['people']['adopt'],'private-family':T['families']['af'],'private-childref':T['families']['af']['child_ref_list'][0]}[target];obj['private']=True
                if target.startswith('conflict'):
                    obj=T['people']['adopt'] if target=='conflict-person' else T['families']['af'];obj['tag_list']=['t']
                self.assertEqual(P.ancestry(T,'child',1)[0].get(2),'p')
                self.assertEqual(P.completeness(T,'child',1)['rows'][0]['occupied_slots'],1)
                self.assertEqual(len(O.report(T,root='child')['rows']),1)
                self.assertEqual([r['person'] for r in P.evidence_fan(T,'child',[])['rows']],['I1'])
