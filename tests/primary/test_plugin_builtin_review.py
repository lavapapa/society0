"""内置机制的非作者消费与恢复审查。"""
import pytest
from tests.primary.test_plugin_round_robin import plugins
from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope,Moment,Ref,Query
from society0.plugins.round_robin import round_robin_plugin


@pytest.mark.asyncio
async def test_review_inbox_count_matches_rows_after_clear_and_round_revisit(tmp_path):
    async with compose(tmp_path/'run',plugins(round_robin_plugin('abcd',group_size=4))) as host:
        mechanism=host.service('conversation','mechanism')
        actions=host.service('interaction','actions')
        mechanism.start_round(1)
        await actions.invoke(InteractionScope('a',Moment(1,'talk')),'conversation.send_message_to_partner',Ref('conversation','participants','a'),{'content':'round1'})
        mechanism.advance_round()
        mechanism.start_round(2)
        mechanism.initialize_round_messages(2)
        before=mechanism.pairing('d')
        with pytest.raises(ValueError,match='backwards'):
            mechanism.start_round(1)
        assert mechanism.pairing('d')==before
        info=host.service('interaction','information')
        page=await info.query(InteractionScope('d',Moment(1,'talk')),'/conversation/messages',Query())
        assert page.total==len(page.items)==len(mechanism.conversation_view('d')['messages'])
        history=await info.query(InteractionScope('d',Moment(1,'talk')),'/conversation/history',Query())
        assert history.total==len(history.items)==1


@pytest.mark.asyncio
async def test_review_broadcast_is_atomic_and_partial_step_is_diagnostic(tmp_path,monkeypatch):
    from society0.kernel.storage import Writer
    plan=plugins(round_robin_plugin('abcd',group_size=4))
    async with compose(tmp_path/'run',plan) as host:
        mechanism=host.service('conversation','mechanism')
        mechanism.start_round(1)
        store=host.service('storage','store')
        store.complete(1)
        original=Writer.execute
        calls=[0]
        def fail(self,sql,bindings=()):
            if sql.startswith('INSERT INTO "conversation_messages"'):
                calls[0]+=1
                if calls[0]==2:raise OSError('second receiver failed')
            return original(self,sql,bindings)
        monkeypatch.setattr(Writer,'execute',fail)
        with pytest.raises(OSError):
            await host.service('interaction','actions').invoke(InteractionScope('a',Moment(1,'p')),'conversation.broadcast_to_group',Ref('conversation','participants','a'),{'content':'full'})
        assert store.read(lambda r:r.query('SELECT count(*) FROM conversation_messages'))==[(0,)]
        assert store.read(lambda r:r.query('SELECT count(*) FROM conversation_counts'))==[(0,)]
        monkeypatch.setattr(Writer,'execute',original)
        await host.service('interaction','actions').invoke(InteractionScope('a',Moment(1,'p')),'conversation.broadcast_to_group',Ref('conversation','participants','a'),{'content':'diagnostic'})
        assert len(mechanism.conversation_view('d')['messages'])==1
    async with compose(tmp_path/'restore',plan,source=tmp_path/'run') as host:
        assert host.service('conversation','mechanism').conversation_view('d')['messages']==[]
