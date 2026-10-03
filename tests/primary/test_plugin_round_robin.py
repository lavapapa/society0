"""轮次对话机制的正式插件、信息、行动与恢复验收。"""
import pytest
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin, InteractionScope, Moment, Ref, Query
from society0.plugins.round_robin import round_robin_plugin


def plugins(*mechanisms):
    return [actor_plugin({'rule':lambda r:None},records=[ActorRecord(x,'rule') for x in 'abcdefgh']),
            interaction_plugin(lambda *a:True),*mechanisms]


@pytest.mark.asyncio
async def test_four_person_circle_pairs_each_pair_once_and_restores(tmp_path):
    plan=plugins(round_robin_plugin('abcd',group_size=4))
    async with compose(tmp_path/'run',plan) as host:
        env=host.service('conversation','mechanism')
        assert env.pairing('a')['current_partner'] is None
        expected=[[('a','d'),('b','c')],[('a','c'),('d','b')],[('a','b'),('c','d')]]
        for round_number,pairs in enumerate(expected,1):
            result=env.start_round(round_number)
            assert result['pairs']==pairs
            assert env.pairing('a')['current_round']==round_number
        assert env.pairing('a')['partner_history']==['d','c','b']
        assert env.start_round(3)['pairs']==[]
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored',plan,source=tmp_path/'run') as host:
        env=host.service('conversation','mechanism')
        assert env.pairing('a')['partner_history']==['d','c','b']
        assert env.advance_round()['status']=='completed'


@pytest.mark.asyncio
async def test_dynamic_actions_partner_broadcast_original_content_and_marker(tmp_path):
    async with compose(tmp_path/'run',plugins(round_robin_plugin('abcd',group_size=4))) as host:
        env=host.service('conversation','mechanism')
        actions=host.service('interaction','actions')
        scope=InteractionScope('a',Moment(1,'talk'))
        target=Ref('conversation','participants','a')
        assert 'conversation.send_message_to_partner' not in [a.name for a in (await actions.find(scope,target)).items]
        env.start_round(1)
        sent=await actions.invoke(scope,'conversation.send_message_to_partner',target,{'content':'完整正文🙂'})
        assert sent.status=='completed' and sent.value['sent_to']=='d'
        broadcast=await actions.invoke(scope,'conversation.broadcast_to_group',target,{'content':'组内原文'})
        assert broadcast.status=='completed' and [x['receiver'] for x in broadcast.value['delivered']]==['b','c','d']
        rejected=await actions.invoke(scope,'conversation.send_message_to_partner',target,{'content':'  '})
        assert rejected.status=='rejected'
        assert (await actions.invoke(scope,'conversation.send_message_to_partner',Ref('conversation','participants','b'),{'content':'伪造'})).status=='rejected'
        marked=await actions.invoke(scope,'conversation.mark_conversation_participant',target,{'marker':'ready'})
        assert marked.status=='completed' and host.service('actors','actors').get_record('a').state['conversation_marker']=='ready'
        info=host.service('interaction','information')
        d=InteractionScope('d',Moment(1,'talk'))
        page=await info.query(d,'/conversation/messages',Query(limit=1))
        assert page.total==2 and page.next_cursor and page.items[0]['sender']=='a'
        body=await info.read(d,'/conversation/content/'+str(page.items[0]['id']),offset=0,size=100)
        assert body.data.decode()=='完整正文🙂'
        env.advance_round()
        assert (await info.query(d,'/conversation/messages',Query())).total==0
        assert (await info.query(d,'/conversation/history',Query())).total==2


@pytest.mark.asyncio
async def test_two_instances_share_identity_without_message_or_pairing_collision(tmp_path):
    plan=plugins(round_robin_plugin('abcd',group_size=2,name='first'),round_robin_plugin('acbd',group_size=2,name='second'))
    async with compose(tmp_path/'run',plan) as host:
        first=host.service('first','mechanism'); second=host.service('second','mechanism')
        first.start_round(1); second.start_round(1)
        assert first.pairing('a')['current_partner']=='b'
        assert second.pairing('a')['current_partner']=='c'
        actions=host.service('interaction','actions'); scope=InteractionScope('a',Moment(0,'p'))
        await actions.invoke(scope,'first.send_message_to_partner',Ref('first','participants','a'),{'content':'only first'})
        info=host.service('interaction','information')
        assert (await info.query(InteractionScope('b',Moment(0,'p')),'/first/messages',Query())).total==1
        assert (await info.query(InteractionScope('c',Moment(0,'p')),'/second/messages',Query())).total==0


def test_invalid_group_sizes_and_duplicate_members_are_rejected():
    for members,size in [('abc',3),('abc',2),('aabc',2)]:
        with pytest.raises(ValueError): round_robin_plugin(members,group_size=size)


@pytest.mark.asyncio
async def test_clear_current_messages_preserves_history_and_precision_originals(tmp_path):
    async with compose(tmp_path/'run',plugins(round_robin_plugin('abcd',group_size=4))) as host:
        env=host.service('conversation','mechanism'); env.start_round(1)
        actions=host.service('interaction','actions')
        a=InteractionScope('a',Moment(1,'p'))
        target=Ref('conversation','participants','a')
        await actions.invoke(a,'conversation.send_message_to_partner',target,{'content':'first original'})
        view=env.conversation_view('d')
        assert [m['content'] for m in view['messages']]==['first original']
        env.initialize_round_messages(1)
        assert env.conversation_view('d')['messages']==[]
        await actions.invoke(a,'conversation.send_message_to_partner',target,{'content':'second original'})
        assert [m['content'] for m in env.conversation_view('d')['messages']]==['second original']
        info=host.service('interaction','information'); d=InteractionScope('d',Moment(1,'p'))
        assert (await info.query(d,'/conversation/messages',Query())).total==1
        assert (await info.query(d,'/conversation/history',Query())).total==2
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored',plugins(round_robin_plugin('abcd',group_size=4)),source=tmp_path/'run') as host:
        assert [m['content'] for m in host.service('conversation','mechanism').conversation_view('d')['messages']]==['second original']


@pytest.mark.parametrize('size',[2,4,6,20])
def test_circle_schedule_matches_existing_builtin_order(size):
    from tests.reference.builtin_algorithms import RoundRobinConversationEnv
    from society0.plugins.round_robin import _schedule
    members=[str(i) for i in range(size)]
    assert _schedule(members)==RoundRobinConversationEnv._build_round_robin_schedule(None,members)


@pytest.mark.asyncio
async def test_current_inbox_query_cost_does_not_replay_historical_messages(tmp_path):
    measured=[]
    for count in (100,10000):
        async with compose(tmp_path/str(count),plugins(round_robin_plugin('abcd',group_size=4))) as host:
            env=host.service('conversation','mechanism'); env.start_round(2)
            store=host.service('storage','store')
            def history(w):
                w.executemany('INSERT INTO conversation_messages(sender,receiver,round,timestamp,body) VALUES(\'a\',\'c\',1,0,?)',((b'old',) for _ in range(count)))
                w.execute('INSERT INTO conversation_counts VALUES(\'c\',0,?)',(count,))
                w.execute('INSERT INTO conversation_counts VALUES(\'c\',1,?)',(count,))
            store.transaction(history)
            actions=host.service('interaction','actions')
            await actions.invoke(InteractionScope('a',Moment(2,'p')),'conversation.send_message_to_partner',Ref('conversation','participants','a'),{'content':'current full original'})
            original=store.read; vm=[0]
            def read(callback,**kwargs):
                def instrument(view):
                    view._connection.set_progress_handler(lambda:vm.__setitem__(0,vm[0]+1) or False,1)
                    return callback(view)
                return original(instrument,**kwargs)
            store.read=read
            page=await host.service('interaction','information').query(InteractionScope('c',Moment(2,'p')),'/conversation/messages',Query(limit=1))
            assert page.total==1 and page.items[0]['round']==2
            measured.append(vm[0])
    print({'history':[100,10000],'current_inbox_vm':measured})
    assert measured[1]<=measured[0]*2


@pytest.mark.asyncio
async def test_large_original_message_range_and_restore_without_other_actor_access(tmp_path):
    import apsw
    body='完整原文🙂'*300000
    plan=plugins(round_robin_plugin('abcd',group_size=4))
    async with compose(tmp_path/'run',plan) as host:
        env=host.service('conversation','mechanism'); env.start_round(1)
        sent=await host.service('interaction','actions').invoke(InteractionScope('a',Moment(1,'p')),'conversation.send_message_to_partner',Ref('conversation','participants','a'),{'content':body})
        path='/conversation/content/'+str(sent.value['message_id'])
        info=host.service('interaction','information')
        before=apsw.status(apsw.SQLITE_STATUS_MEMORY_USED,True)[0]
        chunk=await info.read(InteractionScope('d',Moment(1,'p')),path,offset=800000,size=32)
        peak=apsw.status(apsw.SQLITE_STATUS_MEMORY_USED)[1]
        assert chunk.data==body.encode()[800000:800032] and peak-before<4*1024*1024
        from society0.kernel.interaction import Unavailable
        with pytest.raises(Unavailable): await info.read(InteractionScope('b',Moment(1,'p')),path,size=32)
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restored',plan,source=tmp_path/'run') as host:
        info=host.service('interaction','information')
        pieces=[];offset=0
        while True:
            part=await info.read(InteractionScope('d',Moment(1,'p')),path,offset=offset,size=65536)
            pieces.append(part.data)
            if part.next_offset is None: break
            offset=part.next_offset
        assert b''.join(pieces)==body.encode()


@pytest.mark.asyncio
async def test_repeated_start_does_not_remove_existing_pair_or_duplicate_history(tmp_path):
    async with compose(tmp_path/'run',plugins(round_robin_plugin('abcd',group_size=4))) as host:
        env=host.service('conversation','mechanism')
        env.start_round(1)
        before=env.pairing('a')
        assert env.start_round(1)['pairs']==[]
        assert env.pairing('a')==before
