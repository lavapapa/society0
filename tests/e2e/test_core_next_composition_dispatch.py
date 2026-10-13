"""总公开入口与只读验收器必须接通实际规则案例。"""
import json
import os
import subprocess
import sys
import pytest
from .test_core_next_composition_entrypoint import config,cli,ROOT


@pytest.mark.parametrize(('case','variant'), [('actor-directory','default'),('actor-directory','readonly'),
    ('actor-directory','persona'),('artifact-read','default'),
    *(('recommendation',variant) for variant in ('default','external-selection','external-relations','native','semantic-change','paging')),
    *(('registration',variant) for variant in ('default','re-register','owned')),
    *(('manifest',variant) for variant in ('default','alternate-tree')),
    *(('extensions',variant) for variant in ('default','memory-write','memory-recall','memory-tools','memory-all','runtime-replacement'))])
def test_public_composition_dispatch_and_handoff_verifier_cli(tmp_path,case,variant):
    run,result=cli(tmp_path,'run',config(case=case,variant=variant))
    assert result.returncode==0,result.stdout+result.stderr
    report_path=tmp_path/'verdict.json'
    verdict=subprocess.run([sys.executable,'-m','tests.e2e.core_next_composition_assertions',
        '--case',case,'--run',str(run),'--output',str(report_path)],cwd=ROOT,env=os.environ,text=True,capture_output=True)
    assert verdict.returncode==0,verdict.stdout+verdict.stderr
    report=json.loads(verdict.stdout)
    assert json.loads(report_path.read_text(encoding='utf-8'))==report
    assert report['case']==case and report['variant']==variant
    assert report['checks'] and report['first_difference'] is None
    restored,resumed=cli(tmp_path,'restored',config(case=case,variant=variant),source=run,step=1)
    assert resumed.returncode==0,resumed.stdout+resumed.stderr
    compared=subprocess.run([sys.executable,'-m','tests.e2e.core_next_composition_assertions',
        '--case',case,'--run',str(restored),'--continuous',str(run)],cwd=ROOT,env=os.environ,text=True,capture_output=True)
    assert compared.returncode==0,compared.stdout+compared.stderr
    assert json.loads(compared.stdout)['first_difference'] is None


@pytest.mark.parametrize(('case','variant'), [('registration','install-failure'),('manifest','write-failure'),
    *(('shutdown',variant) for variant in ('runtime-first','worker-first','runtime-error','worker-error'))])
def test_public_composition_dispatch_proves_precise_failure(tmp_path,case,variant):
    run,result=cli(tmp_path,'failed',config(case=case,variant=variant))
    assert result.returncode==1,result.stdout+result.stderr
    if case in ('registration','manifest'):
        (run/'cli-result.json').write_text(json.dumps({'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr}),encoding='utf-8')
    verdict=subprocess.run([sys.executable,'-m','tests.e2e.core_next_composition_assertions',
        '--case',case,'--run',str(run)],cwd=ROOT,env=os.environ,text=True,capture_output=True)
    assert verdict.returncode==0,verdict.stdout+verdict.stderr
    report=json.loads(verdict.stdout)
    assert report['variant']==variant and report['checks'] and report['first_difference'] is None
