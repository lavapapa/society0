"""安装后工件验证必须检查显式指定的真实消费者。"""
from pathlib import Path
import importlib.util,os,subprocess,sys,tempfile,unittest
CHECKER=Path(__file__).with_name('artifacts.test.py')
spec=importlib.util.spec_from_file_location('artifact_checker',CHECKER)
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
class ConsumerRoot(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='g20-consumer-root-',dir=os.environ['TMPDIR']);self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name);self.artifacts=self.base/'artifacts';self.artifacts.mkdir();self.consumer=self.base/'actual-consumer';self.package={'name':'@society0/demo'}
 def installed(self,root=None):return checker.installed_location(self.artifacts,self.package,consumer_root=self.consumer if root is None else root)
 def test_cli_missing_explicit_root_precedes_archive_checks(self):
  candidate=Path(os.environ['ARTIFACT_OLD_CANDIDATE'])
  result=subprocess.run([sys.executable,'-B',str(CHECKER),str(candidate),os.environ['ARTIFACT_EXPECTED_COMMIT'],'--consumer-root',str(self.consumer)],text=True,capture_output=True)
  self.assertNotEqual(result.returncode,0);self.assertIn('ARTIFACT_CONSUMER_ROOT',result.stderr);self.assertNotIn('ARTIFACT_IMPLEMENTATION',result.stderr)
 def test_explicit_physical_separate_consumer(self):
  (self.consumer/'node_modules/@society0/demo').mkdir(parents=True);self.installed()
 def test_missing_root_has_no_colocated_fallback(self):
  (self.artifacts/'consumer/node_modules/@society0/demo').mkdir(parents=True)
  with self.assertRaisesRegex(AssertionError,'ARTIFACT_CONSUMER_ROOT'):self.installed()
 def test_missing_package(self):
  (self.consumer/'node_modules').mkdir(parents=True)
  with self.assertRaises(AssertionError):self.installed()
 def test_package_symlink(self):
  outside=self.base/'outside';outside.mkdir();scope=self.consumer/'node_modules/@society0';scope.mkdir(parents=True);(scope/'demo').symlink_to(outside,target_is_directory=True)
  with self.assertRaises(AssertionError):self.installed()
 def test_scoped_parent_escape(self):
  outside=self.base/'outside';(outside/'demo').mkdir(parents=True);modules=self.consumer/'node_modules';modules.mkdir(parents=True);(modules/'@society0').symlink_to(outside,target_is_directory=True)
  with self.assertRaises(AssertionError):self.installed()
if __name__=='__main__':unittest.main()
# cases: RV3-01-T02
