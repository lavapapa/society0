"""候选输入边界验证元数据和冻结目录，微型归档用于路由测试。"""
from pathlib import Path
import io,json,os,subprocess,sys,tarfile,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
from acceptance_candidate import load_candidate
from release import archive_records
class CandidateInputs(unittest.TestCase):
 def setUp(self):
  self.context=tempfile.TemporaryDirectory(prefix='g20-candidate-',dir=os.environ['TMPDIR']);self.addCleanup(self.context.cleanup);self.base=Path(self.context.name).resolve();self.assertEqual(self.base.parent,Path(os.environ['TMPDIR']).resolve());self.candidate=self.base/'candidate';self.candidate.mkdir();tar=self.candidate/'tarballs';tar.mkdir();self.head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
  for n in range(13):
   source={'implementation':{'package':'@unit-control/'+str(n),'gitCommit':self.head,'dirty':False,'release':True}}
   with tarfile.open(tar/(str(n)+'.tgz'),'w:gz') as archive:
    data=json.dumps(source).encode();info=tarfile.TarInfo('package/SOURCE.json');info.size=len(data);archive.addfile(info,io.BytesIO(data))
  self.release={'release':True,'implementation':{'gitCommit':self.head,'dirty':False},'packages':[str(n) for n in range(13)]};self.manifest={'format':'society-release/v1','release':True,'implementation':self.release['implementation'].copy(),'tarballs':archive_records(tar,self.head)};self.save()
  for name in ['tests','examples','profiles']:(self.candidate/'acceptance-inputs'/name).mkdir(parents=True)
 def save(self):
  (self.candidate/'manifest.json').write_text(json.dumps(self.manifest));(self.candidate/'release.json').write_text(json.dumps(self.release))
 def test_actual_candidate_paths_are_selected(self):
  selected=load_candidate(self.candidate);self.assertEqual(selected['release'],self.release);self.assertEqual(selected['inputs'],self.candidate/'acceptance-inputs');self.assertEqual(selected['packages'],sorted((self.candidate/'tarballs').glob('*.tgz')));self.assertEqual(selected['mode'],'candidate');self.assertEqual(selected['candidate'],self.candidate)
 def test_missing_manifest_fails(self):
  (self.candidate/'manifest.json').unlink()
  with self.assertRaisesRegex(AssertionError,'CANDIDATE_MANIFEST'):load_candidate(self.candidate)
 def test_dirty_or_nonrelease_metadata_fails(self):
  self.manifest['implementation']['dirty']=True;self.release['implementation']['dirty']=True;self.save()
  with self.assertRaisesRegex(AssertionError,'CANDIDATE_IDENTITY'):load_candidate(self.candidate)
  self.manifest['implementation']['dirty']=False;self.release['implementation']['dirty']=False;self.release['release']=False;self.save()
  with self.assertRaisesRegex(AssertionError,'CANDIDATE_IDENTITY'):load_candidate(self.candidate)
 def test_mismatched_implementation_fails(self):
  self.release['implementation']['gitCommit']='different';self.save()
  with self.assertRaisesRegex(AssertionError,'CANDIDATE_IDENTITY'):load_candidate(self.candidate)
 def test_actual_archive_metadata_must_equal_manifest(self):
  self.manifest['tarballs'][0]['bytes']+=1;self.save()
  with self.assertRaisesRegex(AssertionError,'CANDIDATE_ARCHIVES'):load_candidate(self.candidate)
 def test_profiles_required_from_frozen_inputs(self):
  (self.candidate/'acceptance-inputs/profiles').rmdir()
  with self.assertRaisesRegex(AssertionError,'CANDIDATE_INPUTS'):load_candidate(self.candidate)
 def test_actual_cli_rejects_missing_candidate_before_output(self):
  output=self.base/'outside-consumer';result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/acceptance.py'),'--candidate',str(self.base/'missing-candidate'),str(output)],capture_output=True,text=True);self.assertNotEqual(result.returncode,0);self.assertIn('CANDIDATE_MANIFEST',result.stderr);self.assertFalse(output.exists())
if __name__=='__main__':unittest.main()
# cases: RV3-01-T02
