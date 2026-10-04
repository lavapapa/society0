"""研究者原样执行公开教程，结果与恢复使用真实运行产物。"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from examples.core_next.rule_run import build
from society0.kernel.observation import Observation
from society0.kernel.results import Results
from society0.kernel.runner import run_plan
from society0.kernel.storage import StageReader


ROOT = Path(__file__).resolve().parents[2]


def business_results(path):
    with StageReader(path) as reader:
        results = Results(reader)
        rows = []
        for step in (1, 2):
            header = results.phase(step, 0)
            rows.append((results.step(step)['time'], [
                item['value'] for item in results.page(header['tables']['decisions'])['items']]))
        return rows


@pytest.mark.asyncio
async def test_rule_example_continues_remaining_times_and_matches_continuous(tmp_path):
    release = {'commit': 'tutorial-test-source'}
    await run_plan(tmp_path/'continuous', build({'release': release, 'start': 1, 'end': 2}))
    await run_plan(tmp_path/'first', build({'release': release, 'start': 1, 'end': 1}))
    report = await run_plan(tmp_path/'resumed', build({'release': release, 'start': 1, 'end': 2}),
                            source=tmp_path/'first', step=1)
    assert report['complete_step'] == 2
    assert business_results(tmp_path/'resumed') == business_results(tmp_path/'continuous') == [
        (1, [{'actor': 'alice', 'time': 1}, {'actor': 'bob', 'time': 1}]),
        (2, [{'actor': 'alice', 'time': 2}, {'actor': 'bob', 'time': 2}]),
    ]
    manifest = json.loads((tmp_path/'resumed'/'runner.json').read_text())
    assert manifest['contract']['time'] == {'start': 1, 'end': 2}
    assert manifest['source']['step'] == 1


def test_getting_started_commands_run_verbatim_after_installation(tmp_path):
    document = (ROOT/'docs/core-next/getting-started.md').read_text()
    commands = re.findall(r'```sh\n(.*?)\n```', document.split('## 二、运行', 1)[1], re.S)
    assert commands
    environment = {**os.environ, 'PATH': str(Path(sys.executable).parent)+os.pathsep+os.environ['PATH'],
                   'PYTHONPATH': str(ROOT/'src')+os.pathsep+str(ROOT),
                   'SOCIETY0_SOURCE_COMMIT': 'tutorial-test-source'}
    for command in commands:
        completed = subprocess.run(['bash', '-eu', '-c', command], cwd=tmp_path, env=environment,
                                   capture_output=True, text=True)
        assert completed.returncode == 0, completed.stdout+completed.stderr
    path = tmp_path/'runs/first-study/resumed'
    with Observation(path) as reader:
        assert reader.status()['complete']['step'] == 2
    assert [time for time, _ in business_results(path)] == [1, 2]


@pytest.mark.asyncio
async def test_analysis_snippet_reads_all_pages_and_original_large_values(tmp_path, monkeypatch):
    from society0.kernel.results import StepResult
    from tests.primary.test_kernel_runner import plan

    expected = [{'actor': 'alice', 'time': 2, 'text': '完整原文🙂'*20000}, None, False, 2**80, *range(101)]
    study = plan([])
    # 保留既有装配，测试选择的结果页跨多个页并含超出页预算的正文。
    from society0.kernel.schedule import schedule_plugin, SequenceSchedule
    from society0.kernel.runtime import Phase
    study.plugins[-1] = schedule_plugin(SequenceSchedule((10,20), [Phase('decide', lambda ctx: StepResult(
        metrics={'rows': len(expected)}, tables={'decisions': iter(expected)}))]))
    run_dir = tmp_path/'runs/first-study/resumed'
    await run_plan(run_dir, study)
    monkeypatch.chdir(tmp_path)
    document = (ROOT/'skill/references/run-monitor-analyze.md').read_text()
    code = re.findall(r'```python\n(.*?)\n```', document, re.S)[0]
    namespace = {}
    exec(compile(code, 'run-monitor-analyze.md', 'exec'), namespace)
    assert [row['value'] for row in namespace['table_rows']] == expected
    assert namespace['metric_rows'] == [{'step': 2, 'phase': 'decide', 'phase_index': 0,
                                        'name': 'rows', 'value': len(expected)}]
