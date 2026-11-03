from pathlib import Path
import io,json,os,sys,tarfile,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from release_compare import path_leaks
class ArtifactPaths(unittest.TestCase):
    def scan(self,name,content,forbidden=()):
        with tempfile.TemporaryDirectory(prefix='g18-path-gate-') as root:
            file=Path(root)/'sample.tgz'
            with tarfile.open(file,'w:gz') as archive:
                member=tarfile.TarInfo('package/'+name);member.size=len(content);archive.addfile(member,io.BytesIO(content))
            return path_leaks(file,forbidden)
    def test_exact_worktree_byte_substring(self):
        result=self.scan('index.js',b'const source="/work/clone-a/input";',['/work/clone-a'])
        self.assertEqual(result[0]['code'],'ABSOLUTE_PATH_LEAK')
    def test_mnt_data_byte_prefix(self):
        self.assertEqual(self.scan('native.node',b'\x00/mnt/'+b'data/project\x00')[0]['code'],'ABSOLUTE_PATH_LEAK')
    def test_nested_metadata_absolute_value(self):
        result=self.scan('NATIVE-BUILD.json',json.dumps({'nested':[{'path':'/private/build/source'}]}).encode())
        self.assertEqual(result[0]['code'],'ABSOLUTE_PATH_IN_METADATA')
    def test_policy_pointer_and_relative_paths(self):
        data={'policy':['/concurrency','#/schema/node'],'file':'build/native.node','root':'/mnt-other/path'}
        self.assertEqual(self.scan('generated/plugin.meta.json',json.dumps(data).encode()),[])
    def test_host_root_strings_in_all_metadata_locations(self):
        for file in ['SOURCE.json','package.json','generated/config.json']:
            for value in ['/tmp','/home/user','/Users/m','/root','/opt/tool','/var/tmp','/srv','/data','/nfs/x','/scratch/x']:
                with self.subTest(file=file,value=value):
                    self.assertEqual(self.scan(file,json.dumps({'path':value}).encode())[0]['code'],'ABSOLUTE_PATH_IN_METADATA')
    def test_runtime_literal_is_not_metadata(self):
        self.assertEqual(self.scan('index.js',b'const virtualPath="/tmp";'),[])
    def test_real_extras_config_policy_root_is_a_pointer(self):
        content=(Path(os.environ['ARTIFACT_SOURCE_ROOT'])/'.build/extras/generated/plugin.meta.json').read_bytes()
        self.assertEqual(self.scan('generated/plugin.meta.json',content),[])
    def test_pointer_exclusion_retains_exact_byte_scan(self):
        data={'entries':[{'metadata':{'configPolicy':[{'path':'/mnt/'+'data/project'}]}}]}
        result=self.scan('generated/plugin.meta.json',json.dumps(data).encode())
        self.assertIn('ABSOLUTE_PATH_LEAK',[item['code'] for item in result])
    def test_unrelated_path_field_still_rejects_host_root(self):
        data={'nested':{'configPolicy':[{'path':'/root'}]}}
        self.assertEqual(self.scan('generated/plugin.meta.json',json.dumps(data).encode())[0]['code'],'ABSOLUTE_PATH_IN_METADATA')
if __name__=='__main__':unittest.main()

# cases: RV3-01-T02
