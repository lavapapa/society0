"""Next 当前合同：逐条消息、主体信息、社会事实与新进程恢复。"""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from tests.primary.process_support import ROOT, clean_env


def run_process(script, path, *, source=None, start=None, stop=None, kind=None):
    output = path.with_suffix('.json')
    command = [sys.executable, str(ROOT / 'tests/e2e' / script),
               '--output', str(output), '--run', str(path)]
    for flag, value in [('source', source), ('start', start), ('stop', stop), ('kind', kind)]:
        if value is not None:
            command += ['--' + flag, str(value)]
    result = subprocess.run(command, cwd=path.parent, env=clean_env(ROOT / 'src'),
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(output.read_text())


def next_rr(path, **kwargs):
    return run_process('basic_env_process.py', path, **kwargs)


def next_social(path, **kwargs):
    return run_process('social_process.py', path, **kwargs)


def test_next_plain_run_plan_and_new_process_restore(tmp_path):
    expected = [{'tick': tick, 'state': {}, 'actors': list('abcd')} for tick in range(3)]
    assert next_rr(tmp_path / 'plain', kind='plain') == expected
    first = next_rr(tmp_path / 'plain-first', kind='plain', stop=1)
    rest = next_rr(tmp_path / 'plain-rest', kind='plain', source=tmp_path / 'plain-first')
    assert first + rest == expected


def assert_rr_contract(steps):
    pairs = [[['a', 'd'], ['b', 'c']], [['a', 'c'], ['d', 'b']], [['a', 'b'], ['c', 'd']]]
    partners = {'a': ['d', 'c', 'b'], 'b': ['c', 'd', 'a'],
                'c': ['b', 'a', 'd'], 'd': ['a', 'b', 'c']}
    messages = []
    for number, step in enumerate(steps, 1):
        assert step['round'] == number
        assert step['pairs'] == pairs[number - 1]
        current = [{'sender': actor, 'receiver': partners[actor][number - 1],
                    'round': number, 'content': f'r{number}:{actor}:私信全文'} for actor in 'abcd']
        current += [{'sender': 'a', 'receiver': actor, 'round': number,
                     'content': f'r{number}:广播全文'} for actor in 'bcd']
        messages += current
        assert step['messages'] == messages
        assert step['action_results'] == [
            {'status': 'completed', 'value': {'sent_to': partners[actor][number - 1],
                                            'message_id': (number - 1) * 7 + i + 1}}
            for i, actor in enumerate('abcd')
        ] + [{'status': 'completed', 'value': {'round': number, 'delivered': [
            {'receiver': actor, 'message_id': (number - 1) * 7 + i + 5}
            for i, actor in enumerate('bcd')]}}]
        assert step['inbox'] == {a: [m for m in current if m['receiver'] == a] for a in 'abcd'}
        assert step['actors'] == {actor: {
            'partner': partners[actor][number - 1], 'history': partners[actor][:number],
            'upcoming': [{'round': r, 'partner': partners[actor][r - 1]} for r in range(number, 4)],
            'can_converse': True, 'round': number, 'members': list('abcd'), 'total_rounds': 3,
            'duration': 10, 'marker': f'ready-{number}',
            'actor_record': {'persona': f'{actor}：谨慎的讨论参与者，保留完整个人设定。',
                             'state': {'opinion': f'{actor}的独立判断', 'confidence': i + 1,
                                       'conversation_marker': f'ready-{number}'}}}
            for i, actor in enumerate('abcd')}


def test_next_rr_per_message_actor_information_and_process_restore(tmp_path):
    continuous = next_rr(tmp_path / 'continuous')
    assert_rr_contract(continuous)
    first = next_rr(tmp_path / 'first', stop=1)
    remaining = next_rr(tmp_path / 'restored', source=tmp_path / 'first', start=2)
    assert first + remaining == continuous


def assert_social_contract(record):
    actions, steps = record['actions'], record['steps']
    assert [a['name'] for a in actions] == [
        'follow', 'follow', 'follow', 'follow', 'publish_post', 'publish_post',
        'like_post', 'like_post', 'like_post', 'like_post', 'comment', 'repost',
        'get_trending_posts', 'publish_post', 'unfollow', 'get_trending_posts',
        'publish_post', 'get_trending_posts']
    assert all(a['result_status'] == 'completed' for a in actions)
    expected_edges = [['a', 'b'], ['b', 'c'], ['c', 'a'], ['d', 'a']]
    for index, (action, post_count, notice_count) in enumerate(zip(actions,
            [0, 0, 0, 0, 1, 2, 2, 2, 2, 2, 2, 3, 3, 4, 4, 4, 5, 5],
            [1, 2, 3, 4, 4, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 10, 10, 10])):
        state = action['state']
        assert list(state['posts']) == [f'post_{i}' for i in range(1, post_count + 1)]
        assert state['edges'] == expected_edges[:min(index + 1, 4) if index < 14 else 3]
        assert sum(len(entries) for entries in state['notifications'].values()) == notice_count
    for index, likes in [(6, ['b']), (7, ['b', 'c']), (8, ['b', 'c', 'd'])]:
        post = actions[index]['state']['posts']['post_1']
        assert post['likes'] == likes and post['like_count'] == len(likes)
        assert post['like_events'] == [{'agent_id': a, 'created_tick': 0} for a in likes]
    for index in (0, 1, 2, 3, 6, 7, 8, 9, 14):
        assert actions[index]['result']['changed'] is True
    for index, identifier in [(4, 'post_1'), (5, 'post_2'), (11, 'post_3'),
                              (13, 'post_4'), (16, 'post_5')]:
        assert actions[index]['result']['post_id'] == identifier
        assert identifier in actions[index]['state']['posts']
    reply = actions[10]['state']['posts']['post_1']['replies'][0]
    assert actions[10]['result']['reply_id'] == reply['reply_id']
    assert reply['content'] == '评论全文' and reply['author_id'] == 'b'
    assert [s['tick'] for s in steps] == [0, 1, 2]
    assert [s['active_pool'] for s in steps] == [
        ['post_1', 'post_3'], ['post_4', 'post_1'], ['post_5', 'post_1']]
    assert [s['trending_ids'] for s in steps] == [['post_1', 'post_2']] * 3
    assert [s['pending_exposures'] for s in steps] == [
        {'post_1': 2, 'post_2': 1}, {'post_4': 1, 'post_1': 2, 'post_2': 1},
        {'post_5': 1, 'post_1': 2, 'post_2': 1}]
    assert [[p['post_id'] for p in s['ranking']] for s in steps] == [
        ['post_1'], ['post_1', 'post_4'], ['post_1', 'post_5']]
    for index in (12, 15, 17):
        posts = actions[index]['result']['posts']
        assert [p['post_id'] for p in posts] == ['post_1', 'post_2']
        assert posts[0]['content'] == '旧热帖 #topic 原文'
        assert posts[0]['author_id'] == 'a' and posts[0]['like_count'] == 3
        assert posts[0]['reply_count'] == 1
    final = record['final']
    assert final['edges'] == [['a', 'b'], ['b', 'c'], ['c', 'a']]
    assert [p['content'] for p in final['posts'].values()] == [
        '旧热帖 #topic 原文', '次热帖 原文', '转发全文\n\n--- 原帖 post_1 ---\n旧热帖 #topic 原文',
        '新冷帖 原文', '最新冷帖 原文']
    assert [p['view_count'] for p in final['posts'].values()] == [6, 3, 0, 1, 1]
    for identifier, author, tick, likes, replies, reposts, parent, tags, flags in [
            ('post_1', 'a', 0, ['b', 'c', 'd'], [reply], 1, None, ['topic'], ['flagged']),
            ('post_2', 'b', 0, ['c'], [], 0, None, [], []),
            ('post_3', 'd', 0, [], [], 0, 'post_1', ['topic'], ['flagged']),
            ('post_4', 'c', 1, [], [], 0, None, [], []),
            ('post_5', 'b', 2, [], [], 0, None, [], [])]:
        post = final['posts'][identifier]
        assert post == {'post_id': identifier, 'author_id': author, 'created_tick': tick,
                        'content': post['content'], 'view_count': post['view_count'],
                        'reply_to': parent, 'tags': tags, 'special_tags': flags,
                        'likes': likes, 'like_count': len(likes), 'replies': replies,
                        'reply_count': len(replies), 'repost_count': reposts,
                        'like_events': [{'agent_id': a, 'created_tick': 0} for a in likes]}
    notices = sorted((n for entries in final['notifications'].values() for n in entries), key=lambda n: n['id'])
    assert [n['type'] for n in notices] == ['new_follower'] * 4 + ['post_like'] * 4 + ['post_comment', 'post_repost']
    assert notices[-2]['data']['comment_preview'] == '评论全文'
    assert notices[-2]['data']['reply_id'] == reply['reply_id']
    assert notices[-1]['data']['repost_id'] == 'post_3'
    assert notices[-1]['data']['commentary_preview'] == '转发全文'
    expected_notices = [
        ('b', 'new_follower', {'follower_id': 'a'}),
        ('c', 'new_follower', {'follower_id': 'b'}),
        ('a', 'new_follower', {'follower_id': 'c'}),
        ('a', 'new_follower', {'follower_id': 'd'}),
        ('a', 'post_like', {'post_id': 'post_1', 'interactor_id': 'b'}),
        ('a', 'post_like', {'post_id': 'post_1', 'interactor_id': 'c'}),
        ('a', 'post_like', {'post_id': 'post_1', 'interactor_id': 'd'}),
        ('b', 'post_like', {'post_id': 'post_2', 'interactor_id': 'c'}),
        ('a', 'post_comment', {'post_id': 'post_1', 'interactor_id': 'b',
                             'reply_id': 1, 'comment_preview': '评论全文'}),
        ('a', 'post_repost', {'post_id': 'post_1', 'interactor_id': 'd',
                            'repost_id': 'post_3', 'commentary_preview': '转发全文'})]
    assert final['notifications'] == {actor: [
        {'id': i, 'type': kind, 'created_tick': 0, 'data': data}
        for i, (recipient, kind, data) in enumerate(expected_notices, 1) if recipient == actor]
        for actor in 'abcd'}
    assert record['recommended'] == {'d': ['post_1', 'post_5']}
    assert record['intervention']['flagged_post_ids'] == ['post_1', 'post_3']


def test_next_social_actions_notifications_ranking_exposure_and_process_restore(tmp_path):
    continuous = next_social(tmp_path / 'continuous-social')
    assert_social_contract(continuous)
    for index, key, value in [(0, 'changed', False), (4, 'post_id', 'post_wrong'),
                               (11, 'post_id', 'post_wrong'), (10, 'reply_id', 999)]:
        damaged = deepcopy(continuous)
        damaged['actions'][index]['result'][key] = value
        with pytest.raises(AssertionError):
            assert_social_contract(damaged)
    damaged = deepcopy(continuous)
    damaged['actions'][12]['result']['posts'][0]['content'] = '损坏正文'
    with pytest.raises(AssertionError):
        assert_social_contract(damaged)
    damaged = deepcopy(continuous)
    damaged['actions'][0]['result_status'] = 'rejected'
    with pytest.raises(AssertionError):
        assert_social_contract(damaged)
    first = next_social(tmp_path / 'first-social', stop=1)
    restored = next_social(tmp_path / 'restored-social', source=tmp_path / 'first-social', start=1)
    restored['steps'] = first['steps'] + restored['steps']
    restored['actions'] = first['actions'] + restored['actions']
    assert restored == continuous
