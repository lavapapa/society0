"""发行控制层：真实Git/Node/npm/归档；微型工件明确标记unitControl，产品Native验收另行执行。"""
from pathlib import Path
import importlib.util,io,json,os,shutil,subprocess,sys,tarfile,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import release as producer
FOLDERS=['cosmokit','schemastery','cordis','loader','include','group','sqlite-native','random-native','host','core','extras','social','cli']
BUILDER = r'''from pathlib import Path
import json,subprocess
root=Path.cwd();build=root/'.build';build.mkdir();head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
folders=FOLDERS_LITERAL
for name in folders:
 p=build/name;p.mkdir();files=['entry.js','SOURCE.json','BUILD-LOCK.json','PATCHES','NATIVE-BUILD.json','build','vendor'];(p/'package.json').write_text(json.dumps({'name':'@unit-control/'+name,'version':'1.0.0','files':files}));(p/'entry.js').write_text('export const unitControl = true;');(p/'SOURCE.json').write_text(json.dumps({'unitControl':True,'implementation':{'package':'@unit-control/'+name,'version':'1.0.0','gitCommit':head,'dirty':False,'release':True}}));(p/'BUILD-LOCK.json').write_text('{}');(p/'PATCHES').mkdir();(p/'PATCHES/series.json').write_text('{}')
 if name in ['sqlite-native','random-native']:
  outputs=['build/sqlite_native.node','vendor/libsqlite3.so'] if name=='sqlite-native' else ['build/random_native.node'];(p/'NATIVE-BUILD.json').write_text(json.dumps({'unitControl':True,'outputs':outputs}))
  for relative in outputs:
   target=p/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'unit-control-reference-bytes')
for name in ['host','core','schemastery','cordis']:
 p=build/'node_modules/@society0'/name;p.mkdir(parents=True);(p/'package.json').write_text(json.dumps({'name':'@society0/'+name,'version':'0.0.0'}))
acceptance=build/'acceptance';acceptance.mkdir()
for name in ['tests','examples','profiles']:(acceptance/name).mkdir();(acceptance/name/'marker').write_text('frozen unit control input')
(build/'release.json').write_text(json.dumps({'unitControl':True,'implementation':{'gitCommit':head,'dirty':False},'release':True,'packages':folders}))
'''.replace('FOLDERS_LITERAL',repr(FOLDERS))
class ReleaseProducer(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-producer-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.world=Path(self.context.name).resolve();self.assertEqual(self.world.parent,Path(os.environ['TMPDIR']).resolve());self.root=self.world/'repo/typescript';scripts=self.root/'scripts';scripts.mkdir(parents=True);(self.root/'tests').mkdir();(self.root/'tests/pass.test.mjs').write_text("import test from 'node:test';test('owned control case',()=>{});");(scripts/'build.py').write_text(BUILDER)
  for name in ['test-build.py','acceptance_tests.py','release_regression.py']:shutil.copyfile(ROOT/'scripts'/name,scripts/name)
  (scripts/'catalog-config.mjs').write_text("console.log('owned catalog control');");(scripts/'catalog-reconcile.py').write_text("print('owned catalog control')");(self.root/'package-lock.json').write_text(json.dumps({'packages':{}}));modules=self.root/'node_modules';modules.mkdir();(modules/'.package-lock.json').write_text(json.dumps({'packages':{}}));(self.root.parent/'.gitignore').write_text('/typescript/node_modules\n/typescript/.build/\n');self.git(['init','-q']);self.commit();self.output=self.world/'evidence/release/control'
 def git(self,args):return subprocess.check_output(['git',*args],cwd=self.root.parent,env={**os.environ,'GIT_CONFIG_GLOBAL':'/dev/null'},stderr=subprocess.STDOUT,text=True).strip()
 def commit(self):self.git(['add','-A']);self.git(['-c','user.name=fixture','-c','user.email=fixture@invalid','commit','-qm','owned unit-control inputs']);self.head=self.git(['rev-parse','HEAD'])
 def context_for_output(self):
  context=producer.request(self.root,self.head,self.output);self.output.mkdir(parents=True);return context
 def test_full_owned_control_pipeline(self):
  context=self.context_for_output()
  for side in ['a','b']:producer.build_side(context,side)
  manifest=producer.publish_manifest(context);self.assertEqual(len(manifest['tarballs']),13);self.assertEqual(manifest['implementation'],{'gitCommit':self.head,'dirty':False});self.assertTrue(all(not path.exists() for path in context['worktrees'].values()));self.assertTrue((self.output/'manifest.json').is_file());self.assertFalse((self.output/'a/tarballs').exists());self.assertFalse((self.output/'b/tarballs').exists());self.assertEqual(json.loads((self.output/'reproducibility.json').read_text())['status'],'identical');self.assertEqual((self.output/'acceptance-inputs/profiles/marker').read_text(),'frozen unit control input');self.assertEqual((self.output/'native-reference/sqlite-native/vendor/libsqlite3.so').read_bytes(),(self.output/'native-reference-b/sqlite-native/vendor/libsqlite3.so').read_bytes());self.assertEqual(json.loads((self.output/'a/regression/resources.json').read_text())[0]['exitCode'],0)
 def test_real_catalog_failure_keeps_diagnostics_and_stops_before_pack(self):
  (self.root/'scripts/catalog-config.mjs').write_text("console.error('owned stale catalog');process.exit(1);");self.commit();context=self.context_for_output()
  with self.assertRaisesRegex(RuntimeError,'RELEASE_CATALOG_STALE'):producer.build_side(context,'a')
  self.assertTrue(context['worktrees']['a'].is_dir());self.assertFalse(context['worktrees']['b'].exists());self.assertFalse((self.output/'manifest.json').exists());self.assertFalse((self.output/'a/tarballs').exists());self.assertEqual(json.loads((self.output/'a/catalog-config.log.json').read_text())['exitCode'],1);self.assertIn('owned stale catalog',(self.output/'a/catalog-config.log').read_text())
 def cohorts(self,different=False,dirty=False):
  context=self.context_for_output()
  (self.output/'release.json').write_text(json.dumps({'release':True,'implementation':{'gitCommit':self.head,'dirty':False}}))
  for side in ['a','b']:
   folder=self.output/side/'tarballs';folder.mkdir(parents=True)
   for n in range(13):
    implementation={'gitCommit':self.head,'dirty':dirty,'release':True}
    with tarfile.open(folder/(str(n)+'.tgz'),'w:gz') as archive:
     for name,data in [('SOURCE.json',json.dumps({'implementation':implementation}).encode()),('entry.js',b'right' if different and side=='b' and n==0 else b'left')]:
      info=tarfile.TarInfo('package/'+name);info.size=len(data);info.mtime=0;archive.addfile(info,io.BytesIO(data))
  return context
 def test_archive_identity_rejects_dirty(self):
  self.cohorts(dirty=True)
  with self.assertRaisesRegex(AssertionError,'RELEASE_PACKAGE_IDENTITY'):producer.archive_records(self.output/'a/tarballs',self.head)
 def test_actual_difference_removes_both_tarball_sets(self):
  context=self.cohorts(different=True)
  with self.assertRaisesRegex(RuntimeError,'RELEASE_NOT_REPRODUCIBLE'):producer.publish_manifest(context)
  self.assertEqual(json.loads((self.output/'reproducibility.json').read_text())['status'],'different');self.assertFalse((self.output/'manifest.json').exists());self.assertTrue(all(not (self.output/side/'tarballs').exists() for side in ['a','b']))
 def test_real_npm_error_preserves_stderr(self):
  (self.root/'.build/bad').mkdir(parents=True);(self.root/'.build/bad/package.json').write_text('{');side=self.world/'pack';side.mkdir()
  with self.assertRaisesRegex(RuntimeError,'RELEASE_PACK_FAILED'):producer.pack_cohort(self.root,['bad'],side)
  self.assertIn('EJSONPARSE',(side/'pack-error.stderr.log').read_text());self.assertTrue((side/'pack-error.stdout.log').is_file())
if __name__=='__main__':unittest.main()
# cases: RV3-01-T01
