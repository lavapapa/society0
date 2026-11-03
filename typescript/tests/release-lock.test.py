"""发行依赖复用必须按既有锁值核对，在校验成功后建立链接。"""
from pathlib import Path
import copy,json,os,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from release_lock import lock_report,prepare_dependencies
KEY='node_modules/example'
RECORD={'version':'1.2.3','resolved':'https://registry.invalid/example.tgz','integrity':'fixture-existing-value'}
def lock(record=None):return {'packages':{'':{'version':'root'},KEY:copy.deepcopy(RECORD if record is None else record)}}
class ReleaseLock(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-release-lock-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.base=Path(self.context.name).resolve();self.assertEqual(self.base.parent,Path(os.environ['TMPDIR']).resolve());self.root=self.base/'worktree';self.root.mkdir();self.shared=self.base/'shared-node-modules';self.shared.mkdir()
 def inputs(self,declared=None,installed=None):
  (self.root/'package-lock.json').write_text(json.dumps(lock() if declared is None else declared));(self.shared/'.package-lock.json').write_text(json.dumps(lock() if installed is None else installed))
 def test_equal_values_and_ignored_extra_entries(self):
  installed=lock();installed['packages']['']={'version':'other-root'};installed['packages']['node_modules/extra']={'version':'unrelated'}
  self.assertEqual(lock_report(lock(),installed),{'matchedEntries':1,'optionalNotInstalled':[]})
 def test_each_existing_field_must_match(self):
  for field in ['version','resolved','integrity']:
   with self.subTest(field=field):
    installed=lock();installed['packages'][KEY][field]='different'
    with self.assertRaisesRegex(AssertionError,'LOCK_TREE_MISMATCH '+KEY+' '+field):lock_report(lock(),installed)
 def test_missing_required_and_dev_required(self):
  for dev in [False,True]:
   declared=lock();declared['packages'][KEY]['dev']=dev
   with self.assertRaisesRegex(AssertionError,'LOCK_TREE_MISMATCH '+KEY):lock_report(declared,{'packages':{}})
 def test_missing_optional_is_reported(self):
  declared=lock();declared['packages'][KEY]['optional']=True
  self.assertEqual(lock_report(declared,{'packages':{}}),{'matchedEntries':0,'optionalNotInstalled':[KEY]})
 def test_installed_optional_is_still_compared(self):
  declared=lock();declared['packages'][KEY]['optional']=True;installed=lock();installed['packages'][KEY]['integrity']='different'
  with self.assertRaisesRegex(AssertionError,'LOCK_TREE_MISMATCH '+KEY+' integrity'):lock_report(declared,installed)
 def test_prepare_links_only_after_success(self):
  self.inputs();report=prepare_dependencies(self.root,self.shared);target=self.root/'node_modules';self.assertTrue(target.is_symlink());self.assertEqual(target.resolve(),self.shared);self.assertEqual(report['matchedEntries'],1)
 def test_mismatch_leaves_no_link(self):
  installed=lock();installed['packages'][KEY]['version']='wrong';self.inputs(installed=installed)
  with self.assertRaisesRegex(AssertionError,'LOCK_TREE_MISMATCH'):prepare_dependencies(self.root,self.shared)
  self.assertFalse((self.root/'node_modules').exists());self.assertFalse((self.root/'node_modules').is_symlink())
 def test_existing_target_is_not_overwritten(self):
  self.inputs();target=self.root/'node_modules';target.write_text('existing target')
  with self.assertRaises(FileExistsError):prepare_dependencies(self.root,self.shared)
  self.assertEqual(target.read_text(),'existing target')
 def test_real_declared_and_installed_cohort(self):
  report=lock_report(json.loads((ROOT/'package-lock.json').read_text()),json.loads((ROOT/'node_modules/.package-lock.json').read_text()));self.assertGreater(report['matchedEntries'],0)
if __name__=='__main__':unittest.main()
# cases: RV3-01-T01
