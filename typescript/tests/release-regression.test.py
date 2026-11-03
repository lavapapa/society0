"""回归采集控制层使用真实Node子进程，完整保留一次同序失败。"""
from pathlib import Path
import json,os,shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
WORKER=ROOT/'scripts/release_regression.py'
assert WORKER.is_file(),'RELEASE_REGRESSION_CAPABILITY_MISSING'
class ReleaseRegression(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-regression-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.base=Path(self.context.name).resolve();self.assertEqual(self.base.parent,Path(os.environ['TMPDIR']).resolve());self.repo=self.base/'repo';self.root=self.repo/'typescript';scripts=self.root/'scripts';scripts.mkdir(parents=True);tests=self.root/'tests';tests.mkdir()
  for name in ['test-build.py','acceptance_tests.py']:
   shutil.copyfile(ROOT/'scripts'/name,scripts/name);self.assertEqual((scripts/name).read_bytes(),(ROOT/'scripts'/name).read_bytes())
  self.payload='full-regression-payload-'*400
  (tests/'a.test.mjs').write_text("import test from 'node:test';\ntest('fixture first',()=>console.log("+json.dumps(self.payload)+"));\n")
  (tests/'b.test.mjs').write_text("import test from 'node:test';\nimport assert from 'node:assert/strict';\ntest('fixture middle failure',()=>assert.fail('retained failure'));\n")
  (tests/'c.test.mjs').write_text("import test from 'node:test';\ntest('fixture last',()=>{});\n")
  (self.repo/'.gitignore').write_text('/typescript/.build/\n')
  for name in ['host','core','schemastery','cordis']:
   folder=self.root/'.build/node_modules/@society0'/name;folder.mkdir(parents=True);(folder/'package.json').write_text(json.dumps({'name':'@society0/'+name,'version':'0.0.0'}))
  self.env={**os.environ,'GIT_CONFIG_GLOBAL':'/dev/null'}
  for args in [['init','-q'],['add','-A'],['-c','user.name=fixture','-c','user.email=fixture@invalid','commit','-qm','owned control fixture']]:subprocess.run(['git',*args],cwd=self.repo,env=self.env,check=True,capture_output=True)
  self.head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.repo,env=self.env,text=True).strip();self.output=self.base/'reports'
 def run_worker(self,head=None):return subprocess.run([sys.executable,'-B',str(WORKER),str(self.root),str(self.output),self.head if head is None else head],env=self.env,capture_output=True,text=True)
 def test_complete_ordered_results_and_full_logs(self):
  result=self.run_worker();self.assertEqual(result.returncode,1,result.stdout+result.stderr);rows=[json.loads(line) for line in result.stdout.splitlines()];self.assertEqual(rows[-1]['summary'],{'files':3,'failed':['b.test.mjs']});self.assertEqual([row['file'] for row in rows[:-1]],['a.test.mjs','b.test.mjs','c.test.mjs'])
  resources=json.loads((self.output/'resources.json').read_text());self.assertEqual([row['exitCode'] for row in resources],[0,1,0]);self.assertTrue(all(0<row['cumulativeChildPeakRssKiB']<=row['capKiB']==524288 for row in resources))
  self.assertIn(self.payload,(self.output/'a.test.mjs.stdout.log').read_text());self.assertNotIn(self.payload,rows[0]['tail']);self.assertIn('retained failure',(self.output/'b.test.mjs.stdout.log').read_text());self.assertTrue((self.output/'c.test.mjs.stderr.log').is_file());self.assertEqual(json.loads((self.output/'start.json').read_text())['head'],self.head)
 def test_wrong_commit_precedes_output_creation(self):
  result=self.run_worker('wrong-commit');self.assertNotEqual(result.returncode,0);self.assertIn('RELEASE_REGRESSION_COMMIT',result.stderr);self.assertFalse(self.output.exists())
 def test_dirty_source_precedes_output_creation(self):
  (self.root/'tests/c.test.mjs').write_text('// tracked source changed\n');result=self.run_worker();self.assertNotEqual(result.returncode,0);self.assertIn('RELEASE_REGRESSION_DIRTY',result.stderr);self.assertFalse(self.output.exists())
 def test_existing_report_is_preserved(self):
  self.output.mkdir();(self.output/'marker').write_text('preserve');result=self.run_worker();self.assertNotEqual(result.returncode,0);self.assertEqual((self.output/'marker').read_text(),'preserve');self.assertFalse((self.output/'start.json').exists())
if __name__=='__main__':unittest.main()
# cases: RV3-01-T01
