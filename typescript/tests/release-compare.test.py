# cases: RV3-01-T05
import gzip,importlib.util,io,json,tarfile,tempfile,unittest
from pathlib import Path
root=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('release_compare',root/'scripts/release_compare.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
assert callable(getattr(module,'compare',None)), 'RELEASE_COMPARE_CAPABILITY_MISSING: path gate exists; complete byte comparator is required'
def archive(content=b'export const value=1;',name='package/dist/index.js',mode=0o644,mtime=0,kind=tarfile.REGTYPE):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as tar:
        member=tarfile.TarInfo(name);member.mode=mode;member.mtime=mtime;member.type=kind;member.size=len(content);tar.addfile(member,io.BytesIO(content))
    return gzip.compress(stream.getvalue(),mtime=0)
class ReleaseCompare(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name);self.a=self.root/'a';self.b=self.root/'b';self.a.mkdir();self.b.mkdir();self.name='society0-probe-00.tgz';data=archive()
        for index in range(13):
            for directory in (self.a,self.b):(directory/f'society0-probe-{index:02}.tgz').write_bytes(data)
    def replace(self,side,data): (side/self.name).write_bytes(data)
    def report(self,forbidden=()):return module.compare(self.a,self.b,forbidden=forbidden)
    def test_identical(self):
        report=self.report();self.assertEqual(report['status'],'identical');self.assertEqual(len(report['tarballs']),13)
        self.assertTrue(all(row['archiveEqual'] and row['differentMembers']==[] for row in report['tarballs']))
    def test_payload_byte(self):
        self.replace(self.b,archive(b'export const value=2;'));report=self.report();self.assertEqual(report['status'],'different');self.assertIn('package/dist/index.js',report['tarballs'][0]['differentMembers'])
    def test_member_metadata(self):
        for change in ({'mode':0o755},{'mtime':1}):
            with self.subTest(change=change):
                self.replace(self.b,archive(**change));report=self.report();self.assertEqual(report['status'],'different');self.assertIn('package/dist/index.js',report['tarballs'][0]['differentMembers'])
    def test_archive_only(self):
        data=bytearray((self.b/self.name).read_bytes());data[4:8]=(1).to_bytes(4,'little');self.replace(self.b,bytes(data));report=self.report();row=report['tarballs'][0];self.assertEqual(report['status'],'different');self.assertEqual(row['differentMembers'],[]);self.assertEqual(row['reason'],'ARCHIVE_ONLY')
    def test_package_set(self):
        empty=self.root/'empty';empty.mkdir();report=module.compare(self.a,empty);self.assertEqual(report['status'],'different');self.assertEqual(report['reason'],'TARBALL_SET')
    def test_requires_thirteen(self):
        a=self.root/'twelve-a';b=self.root/'twelve-b';a.mkdir();b.mkdir()
        for index in range(12):
            for directory in (a,b):(directory/f'probe-{index:02}.tgz').write_bytes(archive())
        report=module.compare(a,b);self.assertEqual(report['status'],'different');self.assertEqual(report['reason'],'TARBALL_SET')
    def test_matching_count_different_names(self):
        other=self.root/'other';other.mkdir()
        for index in range(13):(other/f'other-{index:02}.tgz').write_bytes(archive())
        report=module.compare(self.a,other);self.assertEqual(report['status'],'different');self.assertEqual(report['reason'],'TARBALL_SET')
    def test_member_type(self):
        self.replace(self.a,archive(b'',kind=tarfile.REGTYPE));self.replace(self.b,archive(b'',kind=tarfile.DIRTYPE));report=self.report();self.assertEqual(report['status'],'different');self.assertIn('package/dist/index.js',report['tarballs'][0]['differentMembers'])
    def test_exact_worktree_path(self):
        forbidden='/srv/release-'+self.root.name;data=archive(forbidden.encode())
        for side in (self.a,self.b):self.replace(side,data)
        report=self.report([forbidden]);self.assertEqual(report['status'],'different');self.assertEqual({row['side'] for row in report['pathLeaks'] if row['code']=='ABSOLUTE_PATH_LEAK'},{'a','b'})
    def test_mandatory_root(self):
        for side in (self.a,self.b):self.replace(side,archive(b'/mnt/'+b'data/project'))
        report=self.report();self.assertEqual(report['status'],'different');self.assertEqual({row['side'] for row in report['pathLeaks']},{'a','b'})
    def test_member_set(self):
        self.replace(self.b,archive(name='package/dist/other.js'));row=self.report()['tarballs'][0]
        self.assertEqual(row['differentMembers'],['package/dist/index.js','package/dist/other.js'])
        self.assertEqual(row['memberDetails']['package/dist/index.js']['bPrefix'],[])
        self.assertEqual(row['memberDetails']['package/dist/other.js']['aPrefix'],[])
    def test_native_length(self):
        name='package/build/addon.node';self.replace(self.a,archive(bytes([1]),name));self.replace(self.b,archive(bytes([1,2]),name))
        detail=self.report()['tarballs'][0]['memberDetails'][name]
        self.assertEqual(detail['nativeByteDifferences'],[{'offset':2,'a':None,'b':2}])
    def test_metadata_host_root(self):
        data=archive(json.dumps({'location':'/ho'+'me/fixture'}).encode(),'package/SOURCE.json')
        for side in (self.a,self.b):self.replace(side,data)
        report=self.report();self.assertEqual(report['status'],'different');self.assertTrue(any(row['code']=='ABSOLUTE_PATH_IN_METADATA' for row in report['pathLeaks']))
    def test_config_pointer(self):
        data=archive(json.dumps({'entries':[{'metadata':{'configPolicy':[{'path':'/root'},{'path':'/concurrency'}]}}]}).encode(),'package/generated/plugin.meta.json')
        for side in (self.a,self.b):self.replace(side,data)
        self.assertEqual(self.report()['status'],'identical')
    def test_native_payload(self):
        for suffix in ('node','so'):
            with self.subTest(suffix=suffix):
                name='package/build/addon.'+suffix;a=bytes(range(256));b=bytes(reversed(range(256)))
                self.replace(self.a,archive(a,name));self.replace(self.b,archive(b,name));report=self.report();self.assertEqual(report['status'],'different');self.assertIn(name,report['tarballs'][0]['differentMembers'])
                detail=report['tarballs'][0]['memberDetails'][name];self.assertEqual(detail['aPrefix'],list(a[:200]));self.assertEqual(detail['bPrefix'],list(b[:200]));self.assertEqual(detail['nativeByteDifferences'],[{'offset':i+1,'a':a[i],'b':b[i]} for i in range(50)])
if __name__=='__main__':unittest.main()
