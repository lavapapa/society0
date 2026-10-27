"""独立进程公开 CLI 消费推荐；从 SQL 工件核对全部值和原文。"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from society0.kernel.storage import StageReader

ROOT = Path(__file__).resolve().parents[2]
VARIANTS = ('external-selection', 'external-relations', 'native', 'semantic-change', 'paging', 'default')


def launch(tmp_path, variant, *, source=None, end=1):
    config = dict(release={'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()},
                  case='recommendation', variant=variant, provider='scripted', start=1, end=end if source is None else 2)
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(config))
    output = tmp_path / 'run'
    env = dict(os.environ, PYTHONPATH=f'{ROOT / "src"}:{ROOT}', PYTHONDONTWRITEBYTECODE='1',
               SOCIETY0_RUN_REAL_E2E='0', SOCIETY0_RUN_CORE_REAL='0', ANONYMIZED_TELEMETRY='False')
    command = [sys.executable, '-m', 'society0.kernel.runner', '--factory',
               'examples.core_next.composed_recommendation:build', '--config', str(config_path), '--output', str(output)]
    if source is not None:
        command += ['--source', str(source), '--step', '1']
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
    (tmp_path / 'cli.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    return output


def artifacts(path):
    with StageReader(path) as reader:
        rows = reader.read(lambda r: [json.loads(row[0]) for row in r.iter_query(
            'SELECT body FROM recommendation_observations ORDER BY id')])
        events = reader.read(lambda r: [json.loads(row[0]) for row in r.iter_query(
            'SELECT body FROM recommendation_events ORDER BY id')])
        posts = reader.read(lambda r: r.query('SELECT id,like_count,reply_count,view_count FROM social_posts ORDER BY ordinal'))
    return rows, events, posts


def assert_snapshot(row):
    direct = row['direct']
    assert row['source_revision'] == direct['revision']
    for key in ('following', 'similarities', 'candidates', 'ranked', 'total'):
        assert row[key] == direct[key], (row['label'], key, row[key], direct[key])
    # 消费者按固定研究权重独立核算九项分量，不复用作者 ranker。
    candidates = {item['post_id']: item for item in row['candidates']}
    expected_order = []
    for ranked in row['ranked']:
        candidate = candidates[ranked['post_id']]
        assert candidate['author_id'] != row['actor']
        time = math.exp(-max(row['tick'] - candidate['created_tick'], 0) / 24)
        engagement = candidate['engagement_score']
        network = float(candidate['author_id'] in row['following'])
        semantic = row['similarities'].get(ranked['post_id'], 0.)
        score = .1*time + .2*engagement + .4*network + .3*semantic
        components = dict(time_score=time, time_contribution=.1*time,
            engagement_score=engagement, engagement_contribution=.2*engagement,
            network_score=network, network_contribution=.4*network,
            semantic_score=semantic, semantic_contribution=.3*semantic, total_score=score)
        assert ranked['components'] == {key: round(value, 6) for key, value in components.items()}
        assert ranked['score'] == pytest.approx(score)
        expected_order.append((score, candidate['created_tick'], ranked['post_id']))
    assert row['total'] == sum(item['author_id'] != row['actor'] for item in candidates.values())
    assert [item[2] for item in sorted(expected_order, key=lambda item: item[:2], reverse=True)] == [r['post_id'] for r in row['ranked']]
    if row['page'] is not None:
        page = row['page']
        assert page['total'] == row['total']
        assert page['revision'][0] == row['source_revision']
        ranks = {item['post_id']: item for item in row['ranked']}
        for item in page['items']:
            assert item['_recommendation_score'] == ranks[item['post_id']]['components']


@pytest.mark.e2e
@pytest.mark.parametrize('variant', VARIANTS)
def test_public_recommendation_cli(tmp_path, variant):
    path = launch(tmp_path, variant)
    status = json.loads((path / 'runner-status.json').read_text())
    assert (status['status'], status['complete_step'], status['diagnostic_errors']) == ('completed', 1, 0)
    manifest = json.loads((path / 'runner.json').read_text())
    assert manifest['contract']['configuration']['models'] == {}
    assert manifest['contract']['configuration']['variant'] == variant
    assert 'recommendation.external' in [p['name'] for p in manifest['plugins']]
    rows, events, posts = artifacts(path)
    assert rows
    for row in rows:
        assert_snapshot(row)
        preferred_author = 'd' if variant == 'native' and row['label'] in ('native-persona', 'native-unrelated', 'full-page') else 'b'
        candidates = {c['post_id']: c for c in row['candidates']}
        for item in row['ranked']:
            assert item['components']['semantic_score'] == float(candidates[item['post_id']]['author_id'] == preferred_author)
    indexed = {row['label']: row for row in rows}
    for event in events:
        if event['event'] == 'original-read':
            number = int(event['post_id'].split('_')[1])
            author = 'b' if number == 1 else 'd'
            expected = f'帖子{author} 序号{number}：完整社区讨论、证据与不同观点🙂🚀\n' * 6000
            assert event['content'] == expected
            assert len(event['content'].encode()) > 100000
    if variant == 'external-selection':
        assert [row['total'] for row in rows] == [1, 2, 2, 1]
        assert [row['rank_delta'] for row in rows] == [1, 1, 0, 1]
        assert len({row['table_revisions']['social_posts'] for row in rows}) == 1
        assert [r['post_id'] for r in rows[-1]['ranked']] == ['post_2']
        assert len({row['table_revisions']['recommendation_selected'] for row in rows}) == 3
    elif variant == 'external-relations':
        assert indexed['relations-before']['following'] == []
        after = indexed['relations-after']
        assert after['following'] == ['d'] and after['rank_delta'] == 1
        assert after['ranked'][0]['post_id'] == 'post_2'
        assert rows[0]['table_revisions']['social_edges'] == rows[1]['table_revisions']['social_edges']
        assert [item['components']['network_score'] for item in after['ranked']] == [1., 0.]
    elif variant == 'native':
        assert [row['rank_delta'] for row in rows if row['label'].startswith('native-')] == [1, 1, 1, 1, 1, 1, 0]
        assert indexed['native-before']['total'] == 2 and indexed['native-post']['total'] == 3
        assert indexed['native-follow']['following'] == ['d']
        assert next(c for c in indexed['native-like']['candidates'] if c['post_id'] == 'post_2')['like_count'] == 1
        assert next(c for c in indexed['native-comment']['candidates'] if c['post_id'] == 'post_1')['reply_count'] == 1
        assert indexed['native-before']['similarities'] != indexed['native-persona']['similarities']
        assert posts[0][2] == 1 and posts[1][1] == 1
    elif variant == 'semantic-change':
        rejected = [event for event in events if event['event'] == 'await-rejected']
        assert [event['change'] for event in rejected] == ['selected', 'following', 'posts', 'vectors', 'persona']
        assert len([event for event in events if event['event'] == 'embedding-ready']) == 5
        for event in rejected:
            assert event['error'] == 'ValueError'
            assert 'changed' in event['message']
            assert event['rank_delta'] == 0 and event['embed_delta'] == 1
            assert event['pool_reads_delta'] == 1
            assert event['request_candidates']['candidates'] == event['old_candidates']
            assert event['request_following']['following'] == event['old_following']
            new = indexed['await-new-' + event['change']]
            hit = indexed['await-hit-' + event['change']]
            assert new['source_revision'] > event['old_revision']
            assert new['rank_delta'] == 1 and hit['rank_delta'] == 0
            assert hit['ranked'] == new['ranked']
        assert indexed['await-new-selected']['total'] == 1
        assert indexed['await-new-following']['following'] == ['b']
    elif variant == 'paging':
        assert [indexed[label]['rank_delta'] for label in ('paging-first', 'paging-c', 'paging-a-again')] == [1, 1, 1]
        assert indexed['paging-first']['ranked'] == indexed['paging-a-again']['ranked']
        assert indexed['paging-first']['page']['cursor'] is not None
        assert len([event for event in events if event['event'] == 'cursor-rejected']) == 1
        originals = [event for event in events if event['event'] == 'original-read']
        assert [event['post_id'] for event in originals[:2]] == [item['post_id'] for item in indexed['paging-first']['ranked']]
        assert {event['post_id'] for event in originals} == {'post_1', 'post_2'}
        for event in originals:
            assert event['content'] == event['expected']
            assert len(event['content'].encode()) > 100000 and '🙂🚀' in event['content']
        assert indexed['paging-query']['rank_delta'] == indexed['paging-tick']['rank_delta'] == 1
        exposure, = [event for event in events if event['event'] == 'exposure']
        assert exposure['before'] == [0, 0]
        assert exposure['after'] == [int(key == exposure['presented']) for key in exposure['ids']]
        assert exposure['recommended'] == [exposure['presented']]
    elif variant == 'default':
        assert [row['actor'] for row in rows] == ['a', 'a', 'c', 'c']
        assert [row['rank_delta'] for row in rows] == [1, 0, 1, 0]
        assert rows[-1]['rank_calls'] == 2 and rows[-1]['embed_calls'] == 3
        for first, second in (rows[:2], rows[2:]):
            assert first['page']['total'] == second['page']['total'] == 4
            assert first['page']['cursor'] is not None and second['page']['cursor'] is None
            assert [item['post_id'] for row in (first, second) for item in row['page']['items']] == [item['post_id'] for item in first['ranked']]
    if variant != 'paging':
        assert all(post[3] == 0 for post in posts)
    # 公共验收函数另在新只读进程运行；前面的事实判断保持独立。
    result = subprocess.run([sys.executable, '-c',
        'import json,sys; from examples.core_next.composed_recommendation import validate; '
        'checks=validate(sys.argv[1]); failed=[c["id"] for c in checks if not c["passed"]]; '
        'print(json.dumps({"checks":len(checks),"failed":failed})); sys.exit(bool(failed))', str(path)],
        cwd=ROOT, env=dict(os.environ, PYTHONPATH=f'{ROOT / "src"}:{ROOT}', PYTHONDONTWRITEBYTECODE='1'),
        capture_output=True, text=True, timeout=30)
    (tmp_path / 'validation.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.e2e
@pytest.mark.parametrize('variant', VARIANTS)
def test_recommendation_new_process_restores_complete_point(tmp_path, variant):
    first = tmp_path / 'first'
    first.mkdir()
    source = launch(first, variant, end=2)
    restored = tmp_path / 'restored'
    restored.mkdir()
    path = launch(restored, variant, source=source)
    before, _, _ = artifacts(source)
    after, _, _ = artifacts(path)
    first_step = [r for r in before if r['step'] == 1]
    assert after[:len(first_step)] == first_step
    assert len(after) == len(before)
    assert after[-1]['step'] == 2 and after[-1]['total'] == before[-1]['total']
    for row in after[len(first_step):]:
        assert_snapshot(row)
    manifest = json.loads((path / 'runner.json').read_text())
    original = json.loads((source / 'runner.json').read_text())
    assert manifest['run_id'] != original['run_id']
    assert manifest['source'] == {'path': str(source), 'step': 1}
    status = json.loads((path / 'runner-status.json').read_text())
    assert status['complete_step'] == 2
    from examples.core_next.composed_recommendation import compare, validate
    checks = compare(source, path) + validate(source) + validate(path)
    assert checks and all(check['passed'] for check in checks), [check['id'] for check in checks if not check['passed']]
