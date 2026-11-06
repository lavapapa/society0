"""使用真实npm物理安装的控制包，核实冻结示例与工件检查器的调用位置。"""
from pathlib import Path
import json,os,shutil,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from acceptance_consumer import prepare_installed_assets,installed_plugin_names,profile_lock_command
from acceptance_pack import pack
class InstalledAssets(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-installed-assets-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.base=Path(self.context.name).resolve();self.assertEqual(self.base.parent,Path(os.environ['TMPDIR']).resolve());self.inputs=self.base/'frozen';self.consumer=self.base/'consumer';self.consumer.mkdir();(self.consumer/'package.json').write_text(json.dumps({'name':'unit-control-consumer','version':'1.0.0','private':True,'type':'module'}))
  for folder in ['examples','profiles','tests','scripts']:(self.inputs/folder).mkdir(parents=True)
  shutil.copyfile(ROOT/'examples/profile-lock.mjs',self.inputs/'examples/profile-lock.mjs');(self.inputs/'profiles/marker.json').write_text('{"unitControl":true}');shutil.copyfile(ROOT/'tests/artifacts.test.py',self.inputs/'tests/artifacts.test.py');self.head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
 def test_real_installed_metadata_lock_resolves_from_consumer(self):
  package=self.base/'package';package.mkdir();name='@society0/unit-control';metadata={'unitControl':True,'source':{'gitCommit':self.head,'dirty':False,'patches':['owned-fixture'],'hostAbi':'unit-control'}};(package/'plugin.meta.json').write_text(json.dumps(metadata));(package/'package.json').write_text(json.dumps({'name':name,'version':'1.2.3','files':['plugin.meta.json'],'exports':{'./package.json':'./package.json','./metadata':'./plugin.meta.json'},'society':{'plugin':{'metadata':'./metadata'}}}));tar=self.base/'tarballs';tar.mkdir();archive=pack(self.base,package,tar);result=subprocess.run(['npm','install','--ignore-scripts','--no-audit','--no-fund',str(archive)],cwd=self.consumer,capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertFalse((self.consumer/'node_modules/@society0/unit-control').is_symlink())
  prepare_installed_assets(self.inputs,self.consumer);self.assertEqual(installed_plugin_names(self.consumer),[name]);command=profile_lock_command(self.consumer,'candidate');self.assertNotIn('--allow-dirty',command);result=subprocess.run(command,cwd=self.consumer,capture_output=True,text=True);self.assertEqual(result.returncode,0,result.stdout+result.stderr);lock=json.loads((self.consumer/'module-lock.json').read_text());self.assertEqual(lock['packages'][name],{'version':'1.2.3',**metadata['source']});self.assertEqual((self.consumer/'profile-lock.mjs').read_bytes(),(self.inputs/'examples/profile-lock.mjs').read_bytes());self.assertEqual((self.consumer/'profiles/marker.json').read_text(),'{"unitControl":true}')
 def test_dirty_development_flag_is_explicit(self):
  self.assertIn('--allow-dirty',profile_lock_command(self.consumer,'dirty-development'));self.assertNotIn('--allow-dirty',profile_lock_command(self.consumer,'development'));self.assertEqual(installed_plugin_names(self.consumer),[])
 def test_release_contract_examples_live_at_consumer_root(self):
  for name in ['run-profile.mjs','run-profile-branch.mjs']:shutil.copyfile(ROOT/'examples'/name,self.inputs/'examples'/name)
  prepare_installed_assets(self.inputs,self.consumer)
  for name in ['run-profile.mjs','run-profile-branch.mjs','profile-lock.mjs']:self.assertTrue((self.consumer/name).is_file(),name)
  self.assertEqual(profile_lock_command(self.consumer,'candidate')[1],str(self.consumer/'profile-lock.mjs'))
if __name__=='__main__':unittest.main()
# cases: RV3-01-T02
