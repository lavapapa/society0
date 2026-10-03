"""真实领域路由的只读外部消费者，不安装模型或执行业务步骤。"""
import base64
import pytest
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import Information,interaction_plugin
from society0.kernel.observation import Observation


@pytest.mark.asyncio
async def test_shared_domain_factories_read_original_documents_without_side_effects(tmp_path):
    from society0.plugins.round_robin import round_robin_plugin,round_robin_information
    from society0.plugins.social import social_plugin,social_information
    plugins=[actor_plugin({'rule':lambda record:None},records=[ActorRecord('a','rule'),ActorRecord('b','rule')]),
        interaction_plugin(lambda *a:True),round_robin_plugin('ab',group_size=2),social_plugin('ab')]
    def factory(reader):
        info=Information(lambda *a:True)
        info.mount('/conversation',round_robin_information(reader))
        info.mount('/social',social_information(reader))
        return info
    async with compose(tmp_path/'run',plugins) as host:
        conversation=host.service('conversation','mechanism');conversation.start_round(1)
        message=conversation._send('a','完整对话🙂'*1000,False).value['message_id']
        social=host.service('social','mechanism')
        post=social.execute('publish_post','a','a',{'content':'完整帖子'},0).value['post_id']
        store=host.service('storage','store');store.complete(1)
        revision=store.read(lambda r:r.live_revision)
        with Observation(store.path,information_factory=factory) as observer:
            params={'actor':'b','moment':{'time':1,'phase':'read'}}
            inbox=await observer.query(**params,path='/conversation/messages',query={'limit':1})
            assert inbox['total']==1 and inbox['items'][0]['ref'].key==str(message)
            chunk=await observer.read_document(**params,path='/conversation/content/'+str(message),size=71)
            assert base64.b64decode(chunk['data'])==('完整对话🙂'*1000).encode()[:71]
            posts=await observer.query(**params,path='/social/posts')
            assert posts['items'][0]['ref'].key==post
            content=await observer.read_document(**params,path='/social/content/'+post)
            assert base64.b64decode(content['data']).decode()=='完整帖子'
            with pytest.raises(LookupError):await observer.query(**params,path='/social/feed')
        assert store.read(lambda r:r.live_revision)==revision
        assert social.post_details(post)['view_count']==0
