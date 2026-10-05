"""独立审查 SQL 正文引用的合法值与身份边界。"""
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.interaction import InteractionScope,Moment,Query
from society0.kernel.information_sql import SQLInformation,DatasetSpec,DocumentSpec


@pytest.mark.asyncio
async def test_review_nullable_document_column_keeps_null_value(tmp_path):
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id INTEGER PRIMARY KEY,body TEXT)',),
        initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,NULL)')) as store:
        provider=SQLInformation('world',store,{
            'rows':DatasetSpec('docs','id',('id','body'),documents=(('body','body'),)),
            'body':DocumentSpec('docs','id','body')})
        page=await provider.query(InteractionScope('a',Moment(1,'read')),'/world/rows',Query())
        assert page.total==1 and page.items[0]['body'] is None


@pytest.mark.asyncio
async def test_review_mixed_null_and_large_unicode_documents_page_and_permission(tmp_path):
    import json
    from dataclasses import asdict
    from society0.kernel.interaction import Information,Unavailable
    original='原文🙂"\\\n'*40000
    values=[('a/'+str(n),'alice',None if n%2 else original) for n in range(8)]+[('private','bob',original)]
    with StageStore.create(tmp_path/'run',('CREATE TABLE docs(id TEXT PRIMARY KEY NOT NULL,owner TEXT NOT NULL,body TEXT)',),
        initialize=lambda w:w.executemany('INSERT INTO docs VALUES(?,?,?)',values)) as store:
        authorize=lambda scope:('owner=?',(scope.actor,))
        info=Information(lambda *args:True)
        info.mount('/world',SQLInformation('world',store,{
            'rows':DatasetSpec('docs','id',('id','body'),authorize=authorize,documents=(('body','body'),)),
            'body':DocumentSpec('docs','id','body',authorize=authorize)}))
        bound=info.bound(InteractionScope('alice',Moment(1,'read')))
        cursor=None;seen=[]
        while True:
            page=await bound.query('/world/rows',Query(limit=3,max_bytes=700,cursor=cursor))
            assert page.total==8 and page.items
            assert len(json.dumps(asdict(page),ensure_ascii=False,separators=(',',':')).encode())<=700
            seen.extend(page.items);cursor=page.next_cursor
            if cursor is None:break
        assert [row['id'] for row in seen]==['a/'+str(n) for n in range(8)]
        for number,item in enumerate(seen):
            if number%2:assert item['body'] is None
            else:
                ref=item['body']
                part=await bound.read(ref['path'],offset=9,size=31,expected_revision=ref['expected_revision'])
                assert part.data==original.encode()[9:40]
        ref=seen[0]['body']
        store.transaction(lambda w:w.execute('UPDATE docs SET owner=? WHERE id=?',('bob','a/0')))
        with pytest.raises((Unavailable,ValueError)):
            await bound.read(ref['path'],offset=40,size=31,expected_revision=ref['expected_revision'])
