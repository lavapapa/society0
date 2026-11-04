"""真实冷缓存安装必须包含完整运行闭包并保留嵌套版本。"""
from pathlib import Path
import copy,json,os,subprocess,sys,tarfile,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import acceptance_pack
SPECS=[('lib1','offline-probe-lib','1.0.0',{},'export const value=1;'),('lib2','offline-probe-lib','2.0.0',{},'export const value=2;'),('a','@offline-probe/a','1.0.0',{'offline-probe-lib':'^1.0.0'},"export {value} from 'offline-probe-lib';"),('b','@offline-probe/b','1.0.0',{'offline-probe-lib':'^2.0.0'},"export {value} from 'offline-probe-lib';")]
def execute(base,label,args,cwd,cache):
 env=os.environ.copy();env['npm_config_cache']=str(cache)
 result=subprocess.run(list(map(str,args)),cwd=cwd,env=env,capture_output=True,text=True,timeout=180)
 (base/(label+'.stdout.log')).write_text(result.stdout);(base/(label+'.stderr.log')).write_text(result.stderr)
 assert result.returncode==0,(label,result.returncode,result.stdout,result.stderr)
 return result

def package(path,name,version,dependencies,body):
 path.mkdir();manifest={'name':name,'version':version,'type':'module','exports':'./index.mjs','license':'MIT','dependencies':dependencies}
 (path/'package.json').write_text(json.dumps(manifest));(path/'index.mjs').write_text(body)
 return manifest

def fixture(base):
 archive=base/'fixture-tarballs';archive.mkdir();packages={}
 for key,name,version,dependencies,body in SPECS:
  source=base/('source-'+key);manifest=package(source,name,version,dependencies,body)
  result=execute(base,'fixture-pack-'+key,['npm','pack','--ignore-scripts','--json','--pack-destination',archive],source,base/'fixture-pack-cache');record=json.loads(result.stdout)[0]
  packages[key]={'manifest':manifest,'archive':archive/record['filename'],'record':record}
 deps={packages[key]['manifest']['name']:'file:'+str(packages[key]['archive']) for key in ['a','b']}
 root={'name':'offline-canonical','version':'1.0.0','private':True,'type':'module','dependencies':deps}
 lock={'name':root['name'],'version':root['version'],'lockfileVersion':3,'requires':True,'packages':{'':root}}
 locations=[('node_modules/@offline-probe/a','a'),('node_modules/@offline-probe/b','b'),('node_modules/offline-probe-lib','lib1'),('node_modules/@offline-probe/b/node_modules/offline-probe-lib','lib2')]
 for location,key in locations:
  pkg=packages[key];lock['packages'][location]={'version':pkg['manifest']['version'],'resolved':'file:'+str(pkg['archive']),'integrity':pkg['record']['integrity'],'license':'MIT','dependencies':pkg['manifest']['dependencies']}
 canonical=base/'canonical';canonical.mkdir();(canonical/'package.json').write_text(json.dumps(root));(canonical/'package-lock.json').write_text(json.dumps(lock))
 execute(base,'fixture-install',['npm','install','--offline','--ignore-scripts'],canonical,base/'fixture-install-cache')
 return canonical

def archive_identity(path):
 with tarfile.open(path,'r:gz') as archive:manifest=json.load(archive.extractfile('package/package.json'))
 return manifest['name'],manifest['version']

class AcceptancePack(unittest.TestCase):
 def test_complete_runtime_closure_installs_in_empty_cache_with_nested_versions(self):
  base=Path(tempfile.mkdtemp(prefix='g20-acceptance-pack-',dir=os.environ['TMPDIR'])).resolve();self.assertEqual(base.parent,Path(os.environ['TMPDIR']).resolve());print('retained-real-cold-cache-fixture',base,flush=True)
  canonical=fixture(base);tar=base/'tarballs';tar.mkdir()
  with patch.object(acceptance_pack,'THIRD_PARTY',['@offline-probe/a','@offline-probe/b']),patch.object(acceptance_pack,'TYPED',[]):packages=acceptance_pack.dependencies(canonical,tar)
  self.assertEqual({archive_identity(path) for path in tar.glob('*.tgz')},{(name,version) for _,name,version,_,_ in SPECS},'COLD_CACHE_RUNTIME_CLOSURE must include transitive nested versions')
  project=base/'project';package(project,'@society0/offline-probe','0.1.0',{'@offline-probe/a':'^1.0.0','@offline-probe/b':'^1.0.0'},"import{value as a}from'@offline-probe/a';import{value as b}from'@offline-probe/b';export const values=[a,b];")
  packages=[acceptance_pack.pack(canonical,project,tar),*packages]
  consumer=base/'consumer';consumer.mkdir();(consumer/'package.json').write_text(json.dumps({'name':'offline-outside','version':'1.0.0','private':True,'type':'module'}))
  from acceptance_dependency_lock import prepare_install
  prepare_install(tar,consumer,packages)
  cache=base/'actual-empty-cache';self.assertFalse(cache.exists())
  execute(base,'actual-cold-install',['npm','install','--offline','--ignore-scripts',*packages],consumer,cache)
  execute(base,'actual-npm-ls',['npm','ls','--all'],consumer,cache)
  result=execute(base,'actual-public-versions',['node','--input-type=module','-e',"import assert from'node:assert/strict';import{values}from'@society0/offline-probe';assert.deepEqual(values,[1,2]);console.log(JSON.stringify(values));"],consumer,cache)
  self.assertEqual(json.loads(result.stdout),[1,2])
if __name__=='__main__':unittest.main()
# cases: RV3-01-T01 PR02-T02
