"""实际Native分发内容与独立文件系统布局合同。"""
from pathlib import Path
import copy,importlib.util,json,os,tarfile,tempfile,unittest
spec=importlib.util.spec_from_file_location('artifact_checker',Path(__file__).with_name('artifacts.test.py'))
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
class ArtifactLayout(unittest.TestCase):
    def test_real_native_archive_distribution(self):
        fixture=Path(os.environ['ARTIFACT_NATIVE_FIXTURE']);record=json.loads((fixture/'npm-pack.json').read_text())[0]
        with tarfile.open(fixture/record['filename']) as archive:
            package=json.load(archive.extractfile('package/package.json'));members=set(archive.getnames())
            checker.source_distribution(package,members);checker.provenance_files(archive,members);checker.declared_targets(package,members);checker.declarations(archive,members)
            checker.native_outputs(archive,{'name':'@society0/host'},Path('/unused'))
    def test_runtime_typescript_is_rejected(self):
        with self.assertRaises(AssertionError):checker.source_distribution({'name':'@society0/core'},{'package/src/index.ts'})
    def test_missing_export_is_rejected(self):
        with self.assertRaises(AssertionError):checker.declared_targets({'exports':{'.':{'import':'./dist/index.js'}}},set())
    def test_implementation_record_predicates(self):
        package={'name':'@society0/record-test','version':'0.0.0'}
        implementation={'package':package['name'],'version':package['version'],'gitCommit':'unit-commit','dirty':False,'release':True,'lockfile':'BUILD-LOCK.json'}
        checker.implementation_identity(package,{'implementation':implementation},'unit-commit')
        for key,value in [('package','other'),('version','other'),('gitCommit','other'),('dirty',True),('dirty',0),('release',False),('lockfile','other')]:
            with self.subTest(key=key,value=value):
                changed=copy.deepcopy(implementation);changed[key]=value
                with self.assertRaisesRegex(AssertionError,'ARTIFACT_IMPLEMENTATION'):checker.implementation_identity(package,{'implementation':changed},'unit-commit')
    def test_no_consumer_is_optional(self):
        with tempfile.TemporaryDirectory(prefix='g18-art-layout-',dir=os.environ['TMPDIR']) as directory:checker.installed_location(Path(directory),{'name':'@society0/demo'})
    def test_consumer_requires_actual_package_directory(self):
        with tempfile.TemporaryDirectory(prefix='g18-art-layout-',dir=os.environ['TMPDIR']) as directory:
            root=Path(directory);(root/'consumer/node_modules').mkdir(parents=True)
            with self.assertRaises(AssertionError):checker.installed_location(root,{'name':'@society0/demo'})
    def test_physical_package_directory(self):
        with tempfile.TemporaryDirectory(prefix='g18-art-layout-',dir=os.environ['TMPDIR']) as directory:
            root=Path(directory);(root/'consumer/node_modules/@society0/demo').mkdir(parents=True);checker.installed_location(root,{'name':'@society0/demo'})
    def test_linked_package_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='g18-art-layout-',dir=os.environ['TMPDIR']) as directory:
            root=Path(directory);(root/'outside').mkdir();(root/'consumer/node_modules/@society0').mkdir(parents=True);(root/'consumer/node_modules/@society0/demo').symlink_to(root/'outside',target_is_directory=True)
            with self.assertRaises(AssertionError):checker.installed_location(root,{'name':'@society0/demo'})
if __name__=='__main__':unittest.main()

# cases: RV3-01-T02
