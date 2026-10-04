"""帖子元数据直接发现正文，沿原有权限和有界读取取得完整原文。"""
import json
from dataclasses import asdict
import pytest
from society0.kernel.composition import compose
from society0.kernel.interaction import Information, InteractionScope, Moment, Query, Unavailable
from society0.kernel.storage import ReadView, StageReader
from society0.plugins.social import social_plugin, social_information
from tests.primary.test_plugin_social import plan


@pytest.mark.asyncio
@pytest.mark.parametrize('method',['list','query','read'])
async def test_post_metadata_names_content_without_loading_body(tmp_path,monkeypatch,method):
    async with compose(tmp_path/'run',plan(social_plugin('abc',name='public',content_length_limit=-1))) as host:
        social=host.service('public','mechanism'); info=host.service('interaction','information')
        original='完整冷正文🙂'*100000
        key=social.execute('publish_post','a','a',{'content':original},0).value['post_id']
        scope=InteractionScope('b',Moment(1,'read'))
        def no_body(*args,**kwargs):raise AssertionError('metadata must not load body')
        with monkeypatch.context() as patch:
            patch.setattr(ReadView,'read_blob',no_body)
            if method=='list':item=(await info.list(scope,'/public/posts',limit=1)).items[0]
            elif method=='query':item=(await info.query(scope,'/public/posts',Query(fields=('id',),limit=1))).items[0]
            else:item=json.loads((await info.read(scope,'/public/posts/'+key,size=4096)).data)
        assert item['content_path']=='/public/content/'+key
        chunk=await info.read(scope,item['content_path'],offset=0,size=31)
        assert chunk.data==original.encode()[:31] and chunk.total_bytes==len(original.encode())
        assert social.post_details(key)['view_count']==0


@pytest.mark.asyncio
async def test_content_link_preserves_member_and_resource_read_permissions(tmp_path):
    async with compose(tmp_path/'run',plan(social_plugin('abc'))) as host:
        social=host.service('social','mechanism'); store=host.service('storage','store')
        key=social.execute('publish_post','a','a',{'content':'受权限约束的原文'},0).value['post_id']
        info=Information(lambda scope,operation,ref:not (operation=='read' and ref.kind=='content'))
        info.mount('/social',social_information(store))
        scope=InteractionScope('b',Moment(1,'read'))
        item=(await info.query(scope,'/social/posts',Query())).items[0]
        assert item['content_path']=='/social/content/'+key
        with pytest.raises(Unavailable):await info.read(scope,item['content_path'])
        outsider=InteractionScope('outsider',scope.moment)
        assert (await info.query(outsider,'/social/posts',Query())).items==[]
        with pytest.raises(Unavailable):await info.read(outsider,'/social/posts/'+key)


@pytest.mark.asyncio
async def test_post_content_links_participate_in_page_byte_budget(tmp_path):
    async with compose(tmp_path/'run',plan(social_plugin('abc'))) as host:
        social=host.service('social','mechanism'); info=host.service('interaction','information')
        for index in range(20):social.execute('publish_post','a','a',{'content':str(index)},0)
        scope=InteractionScope('b',Moment(1,'read')); cursor=None;found=[]
        while True:
            page=await info.query(scope,'/social/posts',Query(limit=20,max_bytes=1024,cursor=cursor))
            assert page.total==20
            assert len(json.dumps(asdict(page),ensure_ascii=False,separators=(',',':')).encode())<=1024
            assert all(item['content_path']=='/social/content/'+item['id'] for item in page.items)
            found.extend(item['id'] for item in page.items)
            cursor=page.next_cursor
            if cursor is None:break
        assert len(set(found))==20 and len(found)==20


@pytest.mark.asyncio
async def test_readonly_complete_and_restored_runtime_share_content_discovery(tmp_path):
    plugins=plan(social_plugin('abc'))
    scope=InteractionScope('b',Moment(1,'read'))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('social','mechanism'); store=host.service('storage','store')
        key=social.execute('publish_post','a','a',{'content':'完整点原文🙂'},0).value['post_id']
        store.complete(1)
    with StageReader(tmp_path/'run') as reader:
        info=Information(lambda *args:True);info.mount('/social',social_information(reader))
        saved=(await info.query(scope,'/social/posts',Query())).items
        assert saved[0]['content_path']=='/social/content/'+key
        assert (await info.read(scope,saved[0]['content_path'])).data.decode()=='完整点原文🙂'
    async with compose(tmp_path/'restored',plugins,source=tmp_path/'run') as host:
        info=host.service('interaction','information')
        assert (await info.list(scope,'/social/posts')).items==saved
        assert (await info.read(scope,saved[0]['content_path'])).data.decode()=='完整点原文🙂'
