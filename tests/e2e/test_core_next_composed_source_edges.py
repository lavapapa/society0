"""来源空值、交错主体与 Moment 轮换经实际公开进程验证。"""
import json
import os
import subprocess
import sys
import pytest
from .test_core_next_composition_entrypoint import config,cli,verify,ROOT


@pytest.mark.parametrize('variant',('empty-values','concurrent'))
def test_source_edge_batches_preserve_full_requests_and_restore(tmp_path,variant):
    continuous,result=cli(tmp_path,'continuous',config(variant=variant,sources=['position.a.v1','position.b.v1']))
    assert result.returncode==0,result.stdout+result.stderr
    assert verify(continuous)['first_difference'] is None
    restored,resumed=cli(tmp_path,'restored',config(variant=variant),source=continuous,step=1)
    assert resumed.returncode==0,resumed.stdout+resumed.stderr
    assert verify(restored,compare=continuous)['first_difference'] is None
    program="""import importlib.abc,json,sys
class NoTests(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0]=='tests': raise ImportError('example requires optional test package: '+fullname)
sys.meta_path.insert(0,NoTests())
from examples.core_next.composed_source_edges import compare
checks=compare(sys.argv[1],sys.argv[2])
assert all(item['passed'] for item in checks),checks
print(json.dumps({'checks':len(checks)}))
"""
    result=subprocess.run([sys.executable,'-c',program,str(continuous),str(restored)],cwd=ROOT,env=os.environ,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr


def test_moment_switch_reuses_original_identity_after_intermediate_moment(tmp_path):
    a={'time':1,'phase':'read'};b={'time':2,'phase':'read'}
    first,r1=cli(tmp_path,'first',config(variant='moment-switch',moment=a,end=1))
    assert r1.returncode==0,r1.stdout+r1.stderr
    second,r2=cli(tmp_path,'second',config(variant='moment-switch',moment=b,end=2),source=first,step=1)
    assert r2.returncode==0,r2.stdout+r2.stderr
    final,r3=cli(tmp_path,'final',config(variant='moment-switch',moment=a,end=3),source=second,step=2)
    assert r3.returncode==0,r3.stdout+r3.stderr
    assert verify(first)['first_difference'] is None
    assert verify(second)['first_difference'] is None
    assert verify(final)['first_difference'] is None
