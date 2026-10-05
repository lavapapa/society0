"""真实共享SQL信息投影的完整目录、UTF8分片及权限消费者。"""
import base64
import json
import pytest
from society0.kernel.information_fs import InformationFiles
from society0.kernel.information_sql import SQLInformation,DocumentSpec
from society0.kernel.interaction import Information,InteractionScope,Moment
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
async def test_paged_directory_and_large_document_reassemble_every_byte_and_utf8(tmp_path):
    original=('甲🙂é汉字\n'*15000).encode()
    with StageStore.create(tmp_path/'run',['CREATE TABLE docs(id INTEGER PRIMARY KEY,owner TEXT NOT NULL,body BLOB NOT NULL)'],initialize=lambda w:w.executemany('INSERT INTO docs VALUES(?,?,?)',[(i,'alice',original if i==1 else str(i).encode()) for i in range(1,13)])) as store:
        info=Information(lambda *args:True)
        info.mount('/docs',SQLInformation('docs',store,{'texts':DocumentSpec('docs','id','body',authorize=lambda scope:('owner=?',(scope.actor,)))}))
        scope=InteractionScope('alice',Moment(1,'read'));fs=InformationFiles(info.bound(scope),scope,max_file_bytes=1024,page_size=2)
        async def files(path):
            entries=await fs.callback('list',path);result=[]
            for name,kind,*_ in entries:
                if name=='@manifest.json':continue
                child=path+'/'+name
                result.extend(await files(child) if kind=='directory' else [child])
            return result
        try:
            path='/docs/texts';seen=[]
            while True:
                manifest=json.loads(await fs.callback('read',path+'/@manifest.json'))
                assert manifest['total']==12 and manifest['entries_on_page']<=2
                entries=await fs.callback('list',path)
                seen.extend(name for name,kind,*_ in entries if not name.startswith('@'))
                if manifest['next_directory'] is None:break
                path+='/'+manifest['next_directory']
            assert seen==[str(i) for i in range(1,13)]
            document='/docs/texts/1';manifest=json.loads(await fs.callback('read',document+'/@manifest.json'))
            assert manifest['total_bytes']==len(original) and manifest['part_count']>100
            for key,text in (('bytes_directory',False),('text_directory',True)):
                chunks=[]
                for path in await files(document+'/'+manifest[key]):
                    data=await fs.callback('read',path);assert len(data)<=1024
                    if text:data.decode('utf-8')
                    chunks.append(data if text else base64.b64decode(data))
                assert b''.join(chunks)==original
            old_part=(await files(document+'/'+manifest['text_directory']))[0]
            store.transaction(lambda w:w.execute('UPDATE docs SET body=? WHERE id=1',('changed🙂'.encode(),)))
            with pytest.raises((ValueError,FileNotFoundError)):await fs.callback('read',old_part)
            other_scope=InteractionScope('bob',Moment(1,'read'));other=InformationFiles(info.bound(other_scope),other_scope,max_file_bytes=1024,page_size=2)
            try:
                assert not await other.callback('exists','/docs/texts/1')
                assert json.loads(await other.callback('read','/docs/texts/@manifest.json'))['total']==0
                with pytest.raises(FileNotFoundError):await other.callback('read',old_part)
            finally:await other.close();other_scope.close()
        finally:await fs.close();scope.close()
