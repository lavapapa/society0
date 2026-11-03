"""验证真实npm归档与真实Native编译参照；开发构建身份保持原样。"""
from pathlib import Path
import importlib.util,io,json,os,shutil,subprocess,tarfile,tempfile,unittest
ROOT=Path(os.environ['ARTIFACT_SOURCE_ROOT'])
spec=importlib.util.spec_from_file_location('artifact_checker',Path(__file__).with_name('artifacts.test.py'))
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
class NativeArtifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        retained=os.environ.get('ARTIFACT_NATIVE_FIXTURE')
        cls.fixture=Path(retained) if retained else Path(tempfile.mkdtemp(prefix='g18-artifact-native-',dir=os.environ['TMPDIR']))
        if retained:packed=(cls.fixture/'npm-pack.json').read_text()
        else:
            result=subprocess.run(['npm','pack','--ignore-scripts','--json','--pack-destination',str(cls.fixture)],cwd=ROOT/'.build/random-native',text=True,capture_output=True,check=True)
            packed=result.stdout;(cls.fixture/'npm-pack.json').write_text(packed)
        cls.original=cls.fixture/json.loads(packed)[0]['filename']
        with tarfile.open(cls.original) as archive:cls.package=json.load(archive.extractfile('package/package.json'))
        print(json.dumps({'fixture':str(cls.fixture),'tarball':str(cls.original),'reference':str(ROOT/'.build/native-work')}),flush=True)
    def changed(self,name,removed=None,changed=None):
        target=self.fixture/name
        with tarfile.open(self.original) as source,tarfile.open(target,'w:gz') as output:
            for member in source.getmembers():
                if member.name==removed:continue
                content=source.extractfile(member).read() if member.isfile() else None
                if member.name==changed:
                    if member.name.endswith('NATIVE-BUILD.json'):
                        record=json.loads(content);record['outputs']=[];content=json.dumps(record).encode()
                    else:content=bytes([content[0]^1])+content[1:]
                    member.size=len(content)
                output.addfile(member,io.BytesIO(content) if content is not None else None)
        return target
    def validate(self,file,reference=None):
        with tarfile.open(file) as archive:checker.native_outputs(archive,self.package,reference or ROOT/'.build/native-work')
    def test_real_archive_matches_compiler_reference(self):self.validate(self.original)
    def test_corrupt_binary_is_rejected(self):
        changed=self.changed('corrupt.tgz',changed='package/build/random_native.node')
        with self.assertRaisesRegex(AssertionError,'ARTIFACT_NATIVE_BYTES'):self.validate(changed)
    def test_missing_reference_is_rejected(self):
        reference=Path(tempfile.mkdtemp(prefix='reference-',dir=self.fixture));shutil.copytree(ROOT/'.build/native-work/random-native',reference/'random-native')
        p=reference/'random-native/build/random_native.node';assert reference.parent==self.fixture and p.resolve()==reference.resolve()/'random-native/build/random_native.node';p.unlink()
        with self.assertRaisesRegex(AssertionError,'ARTIFACT_NATIVE_REFERENCE'):self.validate(self.original,reference)
    def test_wrong_output_declaration_is_rejected(self):
        changed=self.changed('declaration.tgz',changed='package/NATIVE-BUILD.json')
        with self.assertRaisesRegex(AssertionError,'ARTIFACT_NATIVE_OUTPUTS'):self.validate(changed)
    def test_missing_record_is_rejected(self):
        changed=self.changed('missing-record.tgz',removed='package/NATIVE-BUILD.json')
        with self.assertRaisesRegex(AssertionError,'ARTIFACT_NATIVE_RECORD'):self.validate(changed)
    def test_missing_binary_is_rejected(self):
        changed=self.changed('missing-binary.tgz',removed='package/build/random_native.node')
        with self.assertRaisesRegex(AssertionError,'ARTIFACT_NATIVE_MEMBER'):self.validate(changed)
if __name__=='__main__':unittest.main()
