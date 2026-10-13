"""公共 CLI 的双来源运行、未发布失败与独立进程恢复。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def cli(tmp_path, name, config, *, source=None, step=None):
    path = tmp_path/name
    frozen = tmp_path/(name+'-config.json')
    frozen.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
    args = [sys.executable, '-m', 'society0.kernel.runner', '--factory', 'examples.core_next.composed_cognition:build',
            '--config', str(frozen), '--output', str(path)]
    if source is not None:
        args += ['--source', str(source), '--step', str(step)]
    result = subprocess.run(args, cwd=ROOT, env=os.environ.copy(), text=True, capture_output=True)
    (tmp_path/(name+'-stdout.log')).write_text(result.stdout, encoding='utf-8')
    (tmp_path/(name+'-stderr.log')).write_text(result.stderr, encoding='utf-8')
    return path, result


def config(**extra):
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    return {'release': {'commit': commit}, 'case': 'multi-source', 'provider': 'scripted', 'end': 2, **extra}


def verify(run, *, case='multi-source', compare=None, failed=None):
    args = [sys.executable, '-m', 'tests.e2e.core_next_composition_assertions', '--case', case, '--run', str(run)]
    if compare is not None: args += ['--continuous', str(compare)]
    if failed is not None: args += ['--failed-run', str(failed)]
    result = subprocess.run(args, cwd=ROOT, env=os.environ.copy(), text=True, capture_output=True)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    report = json.loads(result.stdout)
    assert report['format'] == 1 and report['case'] == case
    assert report['first_difference'] is None and report['checks']
    assert all(check['passed'] and check['location'] for check in report['checks'])
    return report


def test_cost_capture_disabled_keeps_full_thread_and_business_facts(tmp_path):
    from society0.kernel.storage import StageReader
    from society0.kernel.threads import ThreadStore
    from .core_next_composition_assertions import stable_messages
    full,first=cli(tmp_path,'full',config())
    cost,second=cli(tmp_path,'cost',config(capture=False))
    assert first.returncode==second.returncode==0,first.stdout+second.stdout
    projected=[]
    business=[]
    for path in (full,cost):
        with StageReader(path) as reader:
            threads=ThreadStore(reader)
            head=threads.list_threads(limit=1)['items'][0]
            messages=threads.snapshot_messages(head['id'],raw=True)['messages']
            # 单独验证原 Thread 全消息，逐字段除去已知生成时间和各运行Thread identity。
            from society0.kernel.model_messages import history
            typed=history(messages)
            from pydantic_ai.messages import ModelMessagesTypeAdapter
            projected.append(stable_messages(ModelMessagesTypeAdapter.dump_python(typed,mode='json')))
            receipts=reader.read(lambda view:view.query('SELECT actor,thread_id,notice_id FROM "notice.data_receipts" ORDER BY actor,notice_id'))
            assert all(row[1]==head['id'] for row in receipts)
            business.append((reader.read(lambda view:view.complete_step),threads.input_cursor(head['id'],'operating_context'),
                reader.read(lambda view:view.query('SELECT * FROM social_posts ORDER BY id')),
                [(actor,notice) for actor,thread,notice in receipts]))
            if path==cost:
                assert reader.read(lambda view:view.query('SELECT count(*) FROM composition_requests')[0][0])==0
                assert reader.read(lambda view:view.query('SELECT count(*) FROM composition_observations')[0][0])==0
                assert reader.read(lambda view:view.query('SELECT count(*) FROM composition_outcomes')[0][0])==0
    assert projected[0]==projected[1]
    assert business[0]==business[1]


def test_e2e_02_06_two_real_processes_preserve_dual_source_requests_and_exclude_partial_write(tmp_path):
    continuous, first = cli(tmp_path, 'continuous', config())
    assert first.returncode == 0, first.stdout+'\n'+first.stderr
    failed, second = cli(tmp_path, 'failed', config(fault='after-callback-partial-write', fault_step=2))
    assert second.returncode == 1, second.stdout+'\n'+second.stderr
    restored, third = cli(tmp_path, 'restored', config(), source=failed, step=1)
    assert third.returncode == 0, third.stdout+'\n'+third.stderr
    report = verify(restored, compare=continuous, failed=failed)
    assert report['source_commit'] == config()['release']['commit']
    assert {'request-history', 'source-positions', 'receipts', 'exposure', 'failure-boundary'} <= {check['id'] for check in report['checks']}


@pytest.mark.parametrize('variant', ['reordered', 'discarded', 'interview'])
def test_e2e_13_16_alternate_input_selection_and_measurement(tmp_path, variant):
    run, result = cli(tmp_path, 'run', config(variant=variant))
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    verify(run)


@pytest.mark.parametrize('fault', ['source-read', 'append-invalid-context', 'append-invalid-message', 'append-invalid-cursor',
    'provider-error', 'after-step-flush', 'before-complete'])
def test_e2e_04_07_each_named_failure_restores_only_the_previous_complete_point(tmp_path, fault):
    failed, first = cli(tmp_path, 'failed', config(fault=fault, fault_step=2))
    assert first.returncode == 1, first.stdout+'\n'+first.stderr
    restored, second = cli(tmp_path, 'restored', config(), source=failed, step=1)
    assert second.returncode == 0, second.stdout+'\n'+second.stderr
    verify(restored, failed=failed)


@pytest.mark.parametrize('sources', [[], ['notice.main.v1'], ['notice.main.v1', 'social.main.feed.v1']])
def test_e2e_02_17_explicit_empty_omitted_and_ordered_sources(tmp_path, sources):
    run, result = cli(tmp_path, 'run', config(sources=sources))
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    verify(run)


def test_e2e_17_omission_and_reintroduction_preserve_inactive_watermark(tmp_path):
    one, result = cli(tmp_path, 'one', config(case='source-lifecycle', end=1))
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    two, result = cli(tmp_path, 'two', config(case='source-lifecycle', sources=['notice.b.v1'], end=2), source=one, step=1)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    three, result = cli(tmp_path, 'three', config(case='source-lifecycle', end=3), source=two, step=2)
    assert result.returncode == 0, result.stdout+'\n'+result.stderr
    verify(three, case='source-lifecycle')
