"""总公开入口与只读验收器必须接通实际规则案例。"""
import json
import os
import subprocess
import sys
import pytest
from .test_core_next_composition_entrypoint import config,cli,ROOT


@pytest.mark.parametrize(('case','variant'), [('actor-directory','default'),('actor-directory','readonly'),
    ('actor-directory','persona'),('artifact-read','default'),
    *(('recommendation',variant) for variant in ('default','external-selection','external-relations','native','semantic-change','paging'))])
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
