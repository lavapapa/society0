"""发行CLI在创建输出/工作树前核实请求，HEAD归一化使用真实Git。"""
from pathlib import Path
import importlib.util,json,os,shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];W=ROOT.parents[1];WORKER=ROOT/'scripts/release.py'
assert WORKER.is_file(),'RELEASE_PRODUCER_CAPABILITY_MISSING'
sys.path.insert(0,str(ROOT/'scripts'));spec=importlib.util.spec_from_file_location('release_producer',WORKER);producer=importlib.util.module_from_spec(spec);spec.loader.exec_module(producer)
class ReleaseCLI(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-release-cli-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.base=Path(self.context.name).resolve();self.assertEqual(self.base.parent,Path(os.environ['TMPDIR']).resolve());self.release=(W/('evi'+'dence')/'release').resolve();self.output=self.release/self.base.name;self.work=[W/'work'/('release-'+self.output.name+'-'+side) for side in ['a','b']];self.assertFalse(self.output.exists());self.assertTrue(all(not p.exists() for p in self.work));self.addCleanup(self.remove_owned,self.output)
 def remove_owned(self,path):
  self.assertIn(path,[self.output,*self.work]);self.assertEqual(path.resolve(),path)
  if path.exists():shutil.rmtree(path)
 def run_cli(self,commit,output):return subprocess.run([sys.executable,'-B',str(WORKER),commit,str(output)],capture_output=True,text=True)
 def test_outside_release_root_rejected_before_commit(self):
  outside=self.base/'outside';result=self.run_cli('no-such-release-commit',outside);self.assertNotEqual(result.returncode,0);self.assertIn('RELEASE_OUTPUT_LOCATION',result.stderr);self.assertFalse(outside.exists());self.assertFalse(self.output.exists())
 def test_existing_output_preserved_before_commit(self):
  self.output.mkdir(parents=True);marker=self.output/'marker';marker.write_text('preserve');result=self.run_cli('no-such-release-commit',self.output);self.assertNotEqual(result.returncode,0);self.assertIn('RELEASE_OUTPUT_EXISTS',result.stderr);self.assertEqual(marker.read_text(),'preserve');self.assertTrue(all(not p.exists() for p in self.work))
 def test_invalid_commit_has_no_output_or_worktree(self):
  result=self.run_cli('no-such-release-commit',self.output);self.assertNotEqual(result.returncode,0);self.assertIn('RELEASE_COMMIT',result.stderr);self.assertFalse(self.output.exists());self.assertTrue(all(not p.exists() for p in self.work))
 def test_existing_worktree_path_preserved(self):
  path=self.work[0];path.mkdir(parents=True);self.addCleanup(self.remove_owned,path);marker=path/'marker';marker.write_text('preserve');head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();result=self.run_cli(head,self.output);self.assertNotEqual(result.returncode,0);self.assertIn('RELEASE_WORKTREE_EXISTS',result.stderr);self.assertEqual(marker.read_text(),'preserve');self.assertFalse(self.output.exists());self.assertFalse(self.work[1].exists())
 def test_real_head_normalizes_without_side_effects(self):
  world=self.base/'world';root=world/'repo/typescript';root.mkdir(parents=True);(root/'fixture.txt').write_text('owned preflight fixture');env={**os.environ,'GIT_CONFIG_GLOBAL':'/dev/null'}
  for args in [['init','-q'],['add','-A'],['-c','user.name=fixture','-c','user.email=fixture@invalid','commit','-qm','owned preflight fixture']]:subprocess.run(['git',*args],cwd=root.parent,env=env,check=True,capture_output=True)
  head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root.parent,env=env,text=True).strip();output=world/('evi'+'dence')/'release/candidate';result=producer.request(root,'HEAD',output);self.assertEqual(result['commit'],head);self.assertEqual(result['output'],output);self.assertEqual(result['repo'],root.parent);self.assertFalse(output.parent.exists());self.assertFalse((world/'work').exists())
if __name__=='__main__':unittest.main()
# cases: RV3-01-T01
