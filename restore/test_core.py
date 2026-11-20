import unittest,tempfile,pathlib,hashlib,os
from unittest.mock import Mock
from core import *
A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222'
class Tests(unittest.TestCase):
 def test_lost_ack_no_retry(self):
  with tempfile.TemporaryDirectory()as d:
   o=Once(Journal(d),'create-tree');invoke=Mock(return_value=B)
   with self.assertRaises(Unknown):o.call('create-tree',invoke,lambda x:{'ok':True})
   with self.assertRaises(Hold):Once(Journal(d)).call('create-tree',invoke,lambda x:{'ok':True})
   self.assertEqual(invoke.call_count,1);self.assertEqual(len(list(pathlib.Path(d).glob('*'))),1)
 def test_no_secret_in_error_or_journal(self):
  with tempfile.TemporaryDirectory()as d:
   def fail():raise ValueError('PRIVATE_SENTINEL_NOT_A_CREDENTIAL')
   with self.assertRaises(Unknown)as e:Once(Journal(d)).call('owner-login',fail,lambda x:x)
   self.assertNotIn('PRIVATE_SENTINEL',str(e.exception));self.assertNotIn('PRIVATE_SENTINEL',next(pathlib.Path(d).iterdir()).read_text())
 def test_same_uuid_rejected(self):
  with self.assertRaises(Hold):require_clone_identity(A,A,A)
 def test_other_uuid_rejected(self):
  with self.assertRaises(Hold):require_clone_identity(A,B,A)
 def test_graph_exact(self):
  a={'people':{'p1':{'family':'f1'}},'families':{'f1':{'people':['p1']}}};b={'people':{'p1':{'family':'f2'}},'families':a['families']};self.assertTrue(compare_graph(a,a)['match']);self.assertEqual(compare_graph(a,b)['changedNamespaces'],['people'])
 def test_namespace_missing_rejected(self):
  with self.assertRaises(Hold):compare_graph({'people':{}},{'families':{}})
 def test_media_bytes(self):
  with tempfile.TemporaryDirectory()as d:
   p=pathlib.Path(d);(p/'a').write_bytes(b'dummy');m=[{'relative':'a','bytes':5,'sha256':hashlib.sha256(b'dummy').hexdigest()}];self.assertTrue(media_verify(p,m)['allVerified']);(p/'a').write_bytes(b'drift')
   with self.assertRaises(Hold):media_verify(p,m)
 def test_media_path_escape(self):
  with tempfile.TemporaryDirectory()as d:
   with self.assertRaises(Hold):media_verify(pathlib.Path(d),[{'relative':'../x','bytes':1,'sha256':'x'}])
 def test_media_symlink(self):
  with tempfile.TemporaryDirectory()as d:
   p=pathlib.Path(d);(p/'a').symlink_to('/etc/passwd')
   with self.assertRaises(Hold):media_verify(p,[{'relative':'a','bytes':1,'sha256':'x'}])
 def test_no_bootstrap_without_attestation(self):
  with tempfile.TemporaryDirectory()as d:
   a=Mock();a.attest_isolation_and_authority.return_value=False
   with self.assertRaises(Hold):Runner(a,Once(Journal(d)),A).bootstrap()
   a.native_add_clone_admin.assert_not_called()
 def test_bootstrap_order_and_readback(self):
  with tempfile.TemporaryDirectory()as d:
   a=Mock();a.attest_isolation_and_authority.return_value=True;a.native_add_clone_admin.return_value=True;a.native_create_tree.return_value=B;a.native_add_clone_owner.return_value=True;a.native_owner_login.return_value=True;a.native_owner_tree_readback.return_value=B
   self.assertEqual(Runner(a,Once(Journal(d)),A).bootstrap(),B);self.assertEqual([c[0]for c in a.method_calls],['attest_isolation_and_authority','native_add_clone_admin','native_create_tree','native_add_clone_owner','native_owner_login','native_owner_tree_readback'])
 def test_restore_requires_identity(self):
  with tempfile.TemporaryDirectory()as d:
   a=Mock()
   with self.assertRaises(Hold):Runner(a,Once(Journal(d)),A).restore()
   a.native_restore.assert_not_called()
 def test_validator_payload_never_stored(self):
  with tempfile.TemporaryDirectory()as d:
   Once(Journal(d)).call('owner-login',lambda:True,lambda x:{'private':'PRIVATE_SENTINEL_NOT_A_CREDENTIAL'})
   for p in pathlib.Path(d).iterdir():self.assertNotIn('PRIVATE_SENTINEL',p.read_text())
if __name__=='__main__':unittest.main()
