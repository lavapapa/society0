from pathlib import Path
import importlib.util,io,json,os,tarfile,unittest
spec=importlib.util.spec_from_file_location('artifact_checker',Path(__file__).with_name('artifacts.test.py'))
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
ROOT=Path(os.environ['ARTIFACT_SOURCE_ROOT'])
class ArtifactSidecar(unittest.TestCase):
    def check(self,change=None,include=True):
        meta=json.loads((ROOT/'.build/host/generated/plugin.meta.json').read_text());expected=meta['source']['gitCommit']
        if change:change(meta['source'])
        data=io.BytesIO()
        with tarfile.open(fileobj=data,mode='w') as archive:
            if include:
                content=json.dumps(meta).encode();member=tarfile.TarInfo('package/generated/plugin.meta.json');member.size=len(content);archive.addfile(member,io.BytesIO(content))
        data.seek(0)
        with tarfile.open(fileobj=data) as archive:checker.sidecar_identity(archive,expected)
    def test_real_clean_host_source(self):self.check()
    def test_dirty_source_is_rejected(self):
        with self.assertRaisesRegex(AssertionError,'ARTIFACT_SIDECAR_IDENTITY'):self.check(lambda source:source.update(dirty=True))
    def test_missing_dirty_is_rejected(self):
        with self.assertRaisesRegex(AssertionError,'ARTIFACT_SIDECAR_IDENTITY'):self.check(lambda source:source.pop('dirty'))
    def test_other_commit_is_rejected(self):
        with self.assertRaisesRegex(AssertionError,'ARTIFACT_SIDECAR_IDENTITY'):self.check(lambda source:source.update(gitCommit='different'))
    def test_package_without_sidecar(self):self.check(include=False)
if __name__=='__main__':unittest.main()
