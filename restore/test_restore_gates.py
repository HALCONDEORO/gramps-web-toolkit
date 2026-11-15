import unittest,tempfile
from core import Runner,Once,Journal,Hold,Unknown
A='11111111-1111-4111-8111-111111111111';B='22222222-2222-4222-8222-222222222222'
class Adapter:
 def __init__(self):self.valid=True;self.identity=B;self.calls=[];self.flip=None
 def attest_isolation_and_authority(self):self.calls.append('attest');return self.valid
 def native_owner_tree_readback(self):self.calls.append('identity');return self.identity
 def native_restore(self,dry):
  self.calls.append('dryrun'if dry else'APPLY')
  if dry and self.flip=='isolation':self.valid=False
  if dry and self.flip=='identity':self.identity=A
  return True
 def validate_restore_summary(self,r):return {'valid':r is True}
def runner(a,d,once=None):
 r=Runner(a,once or Once(Journal(d)),A);r.tree=B;r.owner_verified=True;return r
class Gates(unittest.TestCase):
 def test_flip_isolation_after_dryrun_no_apply(self):
  with tempfile.TemporaryDirectory()as d:
   a=Adapter();a.flip='isolation'
   with self.assertRaises(Hold):runner(a,d).restore()
   self.assertEqual(a.calls,['attest','identity','dryrun','attest'])
 def test_flip_identity_after_dryrun_no_apply(self):
  with tempfile.TemporaryDirectory()as d:
   a=Adapter();a.flip='identity'
   with self.assertRaises(Hold):runner(a,d).restore()
   self.assertEqual(a.calls,['attest','identity','dryrun','attest','identity'])
 def test_recovery_isolation_rejected(self):
  with tempfile.TemporaryDirectory()as d:
   a=Adapter();a.valid=False
   with self.assertRaises(Hold):runner(a,d).restore(recovery=True)
   self.assertEqual(a.calls,['attest'])
 def test_recovery_identity_rejected(self):
  with tempfile.TemporaryDirectory()as d:
   a=Adapter();a.identity=A
   with self.assertRaises(Hold):runner(a,d).restore(recovery=True)
   self.assertEqual(a.calls,['attest','identity'])
 def test_recovery_gate_after_intent(self):
  for flip in ['isolation','identity']:
   with self.subTest(flip=flip),tempfile.TemporaryDirectory()as d:
    a=Adapter()
    class J(Journal):
     def write(self,phase,data):
      super().write(phase,data)
      if phase=='restore-recovery':
       if flip=='isolation':a.valid=False
       else:a.identity=A
    with self.assertRaises(Hold):runner(a,d,Once(J(d))).restore(recovery=True)
    self.assertNotIn('APPLY',a.calls)
 def test_positive_each_invocation(self):
  with tempfile.TemporaryDirectory()as d:
   a=Adapter();r=runner(a,d);r.restore();r.restore(recovery=True)
   self.assertEqual(a.calls,['attest','identity','dryrun','attest','identity','APPLY','attest','identity','APPLY'])
 def test_apply_lost_ack_no_retry(self):
  with tempfile.TemporaryDirectory()as d:
   a=Adapter();r=runner(a,d,Once(Journal(d),'restore-apply'))
   with self.assertRaises(Unknown):r.restore()
   with self.assertRaises(Hold):r.restore()
   self.assertEqual(a.calls.count('APPLY'),1)
if __name__=='__main__':unittest.main()
