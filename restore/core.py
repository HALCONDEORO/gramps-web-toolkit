"""Import-only isolated test workflow core. No network, subprocess or native imports.
An adapter with independently attested isolation and external authority is mandatory.
No live adapter or command-line execution surface is provided in this candidate.
"""
import pathlib,os,json,hashlib,stat,time
class Hold(Exception):pass
class Unknown(Hold):pass
def need(ok,reason):
 if not ok:raise Hold(reason)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def fsync_dir(p):
 fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(fd)
 finally:os.close(fd)
class Journal:
 """Durable INTENT before request. Never store headers/body/token/person data."""
 def __init__(self,root):
  self.root=pathlib.Path(root);st=self.root.lstat();need(stat.S_ISDIR(st.st_mode)and not stat.S_ISLNK(st.st_mode),'JOURNAL_ROOT')
 def write(self,phase,data):
  need(phase in PHASES or phase in {x+'-result'for x in PHASES},'PHASE_FIXED')
  b=canonical(data);need(len(b)<=8192,'JOURNAL_BOUND');p=self.root/(phase+'.json');fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'wb')as f:f.write(b);f.flush();os.fsync(f.fileno())
  fsync_dir(self.root)
 def exists(self,phase):return (self.root/(phase+'.json')).exists()
PHASES=('bootstrap-admin','create-tree','bootstrap-owner','owner-login','identity-readback','restore-dryrun','restore-apply','snapshot-before','operation-test','restore-recovery','snapshot-after')
class Once:
 def __init__(self,journal,fault_phase=None):
  need(fault_phase is None or fault_phase in PHASES,'FAULT_FIXED');self.j=journal;self.fault=fault_phase
 def call(self,phase,invoke,validate):
  need(phase in PHASES,'PHASE_FIXED');need(not self.j.exists(phase),'PRIOR_INTENT_NO_RETRY')
  self.j.write(phase,{'state':'INTENT','at':time.time()})
  try:result=invoke()
  except BaseException:raise Unknown('REQUEST_FAILED_OR_ACK_UNKNOWN')from None
  # Simulates response lost AFTER server action returned, exactly once.
  if self.fault==phase:raise Unknown('INJECTED_LOST_ACK_NO_RETRY')
  try:summary=validate(result)
  except BaseException:raise Unknown('UNVERIFIED_RESPONSE_NO_RETRY')from None
  self.j.write(phase+'-result',{'state':'VERIFIED','at':time.time(),'validationCompleted':True});return result

def require_clone_identity(original,expected,observed):
 import uuid
 for value in(original,expected,observed):need(str(uuid.UUID(value))==value,'UUID_FORMAT')
 need(original!=expected and observed==expected,'CLONE_IDENTITY_MISMATCH');return {'treeIdentityVerified':True,'distinctFromOriginal':True}

def graph_summary(snapshot):
 """Input is normalized toolkit snapshot: namespace -> handle -> full object.
 Exact object bytes canonicalized. Adapter must supply complete pagination.
 No guessed field stripping, no invented native JSON schema or person output.
 """
 need(type(snapshot)is dict and snapshot,'GRAPH_SHAPE');result={}
 for ns,objects in snapshot.items():
  need(ns in ('people','families','events','places','sources','citations','repositories','media','notes','tags'),'GRAPH_NAMESPACE');need(type(objects)is dict,'GRAPH_MAP')
  need(all(type(h)is str and h and type(v)is dict for h,v in objects.items()),'GRAPH_HANDLES')
  result[ns]={'count':len(objects),'handleSetSHA256':digest(sorted(objects)),'objectsSHA256':digest(objects)}
 return result

def compare_graph(before,after):
 a=graph_summary(before);b=graph_summary(after);need(set(a)==set(b),'GRAPH_NAMESPACE_CHANGE');changes=[ns for ns in a if a[ns]!=b[ns]]
 return {'match':not changes,'changedNamespaces':changes,'before':a,'after':b}

def media_verify(root,manifest):
 """Manifest supplied privately by existing scoped inventory, no media scan.
 Reject lexical/symlink escape, duplicate paths, absent files, sizes/hash drift.
 No path or byte contents are returned in error/report.
 """
 root=pathlib.Path(root);need(root.resolve()==root and root.is_dir(),'MEDIA_ROOT');seen=set();total=0
 for row in manifest:
  need(set(row)=={'relative','bytes','sha256'},'MEDIA_ROW');rel=pathlib.PurePosixPath(row['relative']);need(not rel.is_absolute()and rel.parts and '..'not in rel.parts and '.'not in rel.parts,'MEDIA_PATH')
  need(str(rel)not in seen,'MEDIA_DUPLICATE');seen.add(str(rel));p=root
  for part in rel.parts:
   p=p/part;st=p.lstat();need(not stat.S_ISLNK(st.st_mode),'MEDIA_SYMLINK')
  need(stat.S_ISREG(st.st_mode)and type(row['bytes'])is int and st.st_size==row['bytes'],'MEDIA_SIZE_TYPE');h=hashlib.sha256()
  with p.open('rb')as f:
   for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
  need(h.hexdigest()==row['sha256'],'MEDIA_DIGEST');need(p.stat().st_size==st.st_size and p.stat().st_mtime_ns==st.st_mtime_ns,'MEDIA_CHANGED_DURING_READ');total+=st.st_size
 return {'entries':len(seen),'logicalBytes':total,'manifestSHA256':digest(manifest),'allVerified':True}

class Runner:
 """Future native adapter method names, not invented HTTP endpoint strings.
 Adapter must retain secrets/tokens privately and implement safe deadline custody.
 No retry/reconciliation effects are automatic.
 """
 def __init__(self,adapter,once,original_uuid):self.a=adapter;self.once=once;self.original=original_uuid;self.tree=None;self.owner_verified=False
 def bootstrap(self):
  need(self.a.attest_isolation_and_authority()is True,'LIVE_ATTESTATION_REQUIRED')
  self.once.call('bootstrap-admin',self.a.native_add_clone_admin,lambda r:{'created':r is True}if r is True else (_ for _ in()).throw(Hold('CREATE_NOT_CONFIRMED')))
  tree=self.once.call('create-tree',self.a.native_create_tree,lambda t:require_clone_identity(self.original,t,t))
  self.once.call('bootstrap-owner',lambda:self.a.native_add_clone_owner(tree),lambda r:{'created':r is True}if r is True else (_ for _ in()).throw(Hold('CREATE_NOT_CONFIRMED')))
  self.once.call('owner-login',lambda:self.a.native_owner_login(tree),lambda r:{'ownerLoginConfirmed':r is True}if r is True else (_ for _ in()).throw(Hold('LOGIN_NOT_CONFIRMED')))
  self.once.call('identity-readback',self.a.native_owner_tree_readback,lambda observed:require_clone_identity(self.original,tree,observed))
  self.tree=tree;self.owner_verified=True;return tree
 def restore(self,recovery=False):
  need(self.owner_verified and self.tree is not None,'OWNER_IDENTITY_NOT_VERIFIED');need(self.a.attest_isolation_and_authority()is True,'LIVE_ATTESTATION_REQUIRED');require_clone_identity(self.original,self.tree,self.a.native_owner_tree_readback())
  phase='restore-recovery'if recovery else'restore-apply'
  if not recovery:self.once.call('restore-dryrun',lambda:self.a.native_restore(True),self.a.validate_restore_summary)
  return self.once.call(phase,lambda:self.a.native_restore(False),self.a.validate_restore_summary)

