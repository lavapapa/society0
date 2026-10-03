import json
import shutil

import pytest

from society0.kernel.storage import StageReader, StageStore
from society0.kernel.datasets import DATASET_SCHEMA, Datasets


def test_batch_values_ranges_restore_share_and_source_independence(tmp_path, monkeypatch):
    values = [None, True, 123, {'汉字': '🙂\\"' * 200000}, [], {'x': [1, 2]}]
    with StageStore.create(tmp_path/'run', DATASET_SCHEMA) as store:
        data = Datasets(store)
        ref = data.import_rows('facts', iter(values))
        assert len(list((store.path/'artifacts').iterdir())) == 1
        assert [data.get(ref, n) for n in range(len(values))] == values
        page = data.page(ref, limit=100, max_bytes=1024)
        assert page['total'] == len(values)
        ids=[]
        while True:
            ids.extend(item['ordinal'] for item in page['items'])
            if page['next_cursor'] is None: break
            page=data.page(ref,cursor=page['next_cursor'],limit=100,max_bytes=1024)
        assert ids==list(range(len(values)))
        import society0.kernel.datasets as module
        real=module.zlib.decompress;calls=[]
        monkeypatch.setattr(module.zlib,'decompress',lambda b:(calls.append(len(b)),real(b))[1])
        raw=json.dumps(values[3],ensure_ascii=False,separators=(',',':')).encode()
        part=data.read_payload(ref,3,offset=65530,size=64)
        assert part['data']==raw[65530:65594] and len(calls)==2
        store.complete(1)
        source_artifact=store.path/ref['artifact']
        with StageStore.restore(store.path,tmp_path/'fork') as fork:
            target=fork.path/ref['artifact']
            assert source_artifact.stat().st_ino==target.stat().st_ino
    shutil.rmtree(tmp_path/'run')
    with StageReader(tmp_path/'fork') as reader:
        assert Datasets(reader).get(ref,3)==values[3]
    with StageStore.restore(tmp_path/'fork',tmp_path/'again') as restored:
        assert Datasets(restored).get(ref,0) is None


def test_failed_import_and_attach_are_unpublished_and_collectable(tmp_path):
    with StageStore.create(tmp_path/'run', DATASET_SCHEMA) as store:
        def broken():
            yield {'x':1}
            raise ValueError('producer failed')
        with pytest.raises(ValueError,match='producer'):
            Datasets(store).import_rows('bad',broken())
        assert list((store.path/'artifacts').iterdir())==[]
        def attach(writer,ref): raise ValueError('attach failed')
        with pytest.raises(ValueError,match='attach'):
            Datasets(store).import_rows('bad',[1],attach=attach)
        assert store.read(lambda r:r.query('SELECT * FROM datasets'))==[]
        store.complete(1)
    removed=StageStore.collect_orphans(tmp_path/'run')
    assert len(removed)==1


def test_readonly_materialization_has_one_database_and_no_writer(tmp_path):
    with StageStore.create(tmp_path/'run', DATASET_SCHEMA) as store:
        ref=Datasets(store).import_rows('facts',[{'a':1}])
        store.complete(1)
        with StageStore.prepare_readonly(store.path,tmp_path/'view',step=1,run_id='view:1') as reader:
            assert Datasets(reader).get(ref,0)=={'a':1}
            assert reader.read(lambda r:r.run_id)=='view:1'
        assert not (tmp_path/'view'/'root.sqlite').exists()
        with pytest.raises(Exception,match='read.only'):
            StageStore.open(tmp_path/'view')


def test_restore_cross_filesystem_copy_and_other_link_errors(tmp_path,monkeypatch):
    import errno
    import society0.kernel.storage as module
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        ref=Datasets(store).import_rows('x',[1,2])
        store.complete(1)
    def cross(*args):raise OSError(errno.EXDEV,'cross device')
    monkeypatch.setattr(module.os,'link',cross)
    with StageStore.restore(tmp_path/'run',tmp_path/'copy') as store:
        assert Datasets(store).get(ref,1)==2
        assert (store.path/ref['artifact']).stat().st_ino != (tmp_path/'run'/ref['artifact']).stat().st_ino
    def denied(*args):raise PermissionError('link denied')
    monkeypatch.setattr(module.os,'link',denied)
    with pytest.raises(PermissionError):StageStore.restore(tmp_path/'run',tmp_path/'denied')
    assert not (tmp_path/'denied').exists()


def test_sealing_failure_no_head_old_complete_safe(tmp_path,monkeypatch):
    import society0.kernel.storage as module
    real=module._sync
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        def fail_file(path):
            if path.suffix=='.tmp':raise OSError('file sync failed')
            return real(path)
        monkeypatch.setattr(module,'_sync',fail_file)
        with pytest.raises(OSError,match='sync'):Datasets(store).import_rows('x',[1])
        assert store.read(lambda r:r.query('SELECT * FROM datasets'))==[]
        assert list((store.path/'artifacts').iterdir())==[]
        monkeypatch.setattr(module,'_sync',real)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        assert store.complete_step==0
    with pytest.raises(Exception,match='read.only'):
        with StageStore.prepare_readonly(tmp_path/'run',tmp_path/'readonly'):
            StageStore.restore(tmp_path/'readonly',tmp_path/'bad')


@pytest.mark.asyncio
async def test_catalog_runtime_information_and_restored_readonly_consumer(tmp_path):
    from examples.core_next.immutable_catalog import catalog_plugin, Catalog
    from society0.kernel.composition import compose
    from society0.kernel.interaction import Information, InteractionScope, Moment, Query, Unavailable
    rows=[{'id':1,'body':'原文🙂'*100000},{'id':2,'value':[False,1,None]}]
    async with compose(tmp_path/'run',[catalog_plugin()]) as host:
        store=host.service('storage','store');catalog=host.service('catalog','catalog')
        reference=catalog.replace('a',iter(rows))
        info=Information(lambda scope,operation,ref:True);info.mount('/catalog',catalog)
        with InteractionScope('a',Moment(1,'catalog')) as scope:
            first=await info.query(scope,'/catalog',Query(limit=1))
            assert first.total==2 and first.items[0]['payload_ref']['dataset']==reference
            second=await info.query(scope,'/catalog',Query(limit=1,cursor=first.next_cursor))
            assert second.items[0]['value']==rows[1]
            part=await info.read(scope,'/catalog/0',offset=65530,size=64)
            assert part.data==json.dumps(rows[0],ensure_ascii=False,separators=(',',':')).encode()[65530:65594]
        with InteractionScope('b',Moment(1,'catalog')) as scope:
            with pytest.raises(Unavailable):await info.query(scope,'/catalog',Query())
        store.complete(1)
        with StageStore.prepare_readonly(store.path,tmp_path/'view',step=1) as reader:
            with InteractionScope('a',Moment(1,'catalog')) as scope:
                page=await Catalog(reader).query(scope,'/catalog',Query(limit=2))
                assert page.total==2
