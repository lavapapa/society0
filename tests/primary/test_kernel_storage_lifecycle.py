"""离线恢复包与显式孤儿清理的完整身份边界。"""
import json
import shutil
from pathlib import Path
import pytest
from society0.kernel.storage import StageStore, StorageError
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA


def test_restore_copies_repeated_artifact_once_and_survives_source_removal(tmp_path,monkeypatch):
    source=tmp_path/'source'
    with StageStore.create(source,THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        payload=('不可变资料🙂'*10000).encode()
        ref=store.prepare_artifact([payload])
        threads.register_artifact(tid,'result',ref,actor='a')
        for step in range(1,4):store.complete(step,artifacts=[ref])
    import os
    copied=[];native=os.link
    def copy(src,dst,*args,**kwargs):
        copied.append(Path(src));return native(src,dst,*args,**kwargs)
    monkeypatch.setattr(os,'link',copy)
    target=tmp_path/'target'
    with StageStore.restore(source,target) as restored:
        assert copied.count(source/ref)==1
        assert (target/ref).read_bytes()==payload
        assert restored.source=={'run_id':store.run_id,'step':3}
        assert restored.run_id!=store.run_id
    shutil.rmtree(source)
    with StageStore.open(target) as restored:
        assert (target/ref).read_bytes()==payload
        ThreadStore(restored).append_message(tid,{'role':'user','content':'continued'})
        restored.complete(4)
    with StageStore.restore(target,tmp_path/'again') as again:
        assert ThreadStore(again).read_messages(tid)==[{'role':'user','content':'continued'}]
        assert (again.path/ref).read_bytes()==payload


def test_collect_orphans_requires_offline_owner_and_keeps_all_complete_dependencies(tmp_path):
    path=tmp_path/'run'
    with StageStore.create(path,['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        old=store.prepare_artifact([b'old retained'])
        store.complete(1,artifacts=[old])
        new=store.prepare_artifact([b'new retained'])
        store.complete(2,artifacts=[new])
        orphan=store.prepare_artifact([b'unpublished'])
        temporary=path/'changesets'/'unfinished.tmp';temporary.write_bytes(b'partial')
        with pytest.raises(StorageError,match='writer'):
            StageStore.collect_orphans(path)
        assert (path/orphan).exists()
    removed=StageStore.collect_orphans(path)
    assert set(removed)=={orphan,'changesets/unfinished.tmp'}
    assert (path/old).read_bytes()==b'old retained'
    assert (path/new).read_bytes()==b'new retained'
    with StageStore.restore(path,tmp_path/'earlier',step=1) as restored:
        assert (restored.path/old).read_bytes()==b'old retained'


@pytest.mark.parametrize('damage',['missing','identity'])
def test_collect_orphans_validates_complete_chain_before_any_delete(tmp_path,damage):
    path=tmp_path/'run'
    with StageStore.create(path,['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        ref=store.prepare_artifact([b'complete'])
        descriptor=store.complete(1,artifacts=[ref])
        orphan=store.prepare_artifact([b'keep on failure'])
    if damage=='missing':(path/descriptor['changeset']).unlink()
    else:
        marker=path/'steps'/'00000000000000000001.json'
        body=json.loads(marker.read_text());body['run_id']='other';marker.write_text(json.dumps(body))
    with pytest.raises(StorageError):StageStore.collect_orphans(path)
    assert (path/orphan).read_bytes()==b'keep on failure'


def test_foreign_root_identity_rejects_export_and_gc_without_deleting_files(tmp_path):
    schema=['CREATE TABLE item(id INTEGER PRIMARY KEY)']
    with StageStore.create(tmp_path/'run',schema) as store:
        orphan=store.prepare_artifact([b'unpublished'])
    with StageStore.create(tmp_path/'other',schema):pass
    shutil.copyfile(tmp_path/'other'/'root.sqlite',tmp_path/'run'/'root.sqlite')
    with pytest.raises(StorageError,match='root'):
        StageStore.collect_orphans(tmp_path/'run')
    assert (tmp_path/'run'/orphan).exists()
    with pytest.raises(StorageError,match='root'):
        StageStore.restore(tmp_path/'run',tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_repeated_artifact_descriptor_conflict_rejects_unpublished_export(tmp_path):
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        ref=store.prepare_artifact([b'retained'])
        store.complete(1,artifacts=[ref]);store.complete(2,artifacts=[ref])
    path=tmp_path/'run'/'steps'/'00000000000000000002.json'
    descriptor=json.loads(path.read_text());descriptor['artifacts'][0]['size']+=1
    path.write_text(json.dumps(descriptor))
    with pytest.raises(StorageError,match='artifact'):
        StageStore.restore(tmp_path/'run',tmp_path/'target')
    assert not (tmp_path/'target').exists()


def test_latest_descriptor_filename_and_declared_step_must_agree_before_gc(tmp_path):
    path=tmp_path/'run'
    with StageStore.create(path,['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        first=store.prepare_artifact([b'first']);store.complete(1,artifacts=[first])
        second=store.prepare_artifact([b'second']);store.complete(2,artifacts=[second])
    marker=path/'steps'/'00000000000000000002.json'
    body=json.loads(marker.read_text());body['step']=1;marker.write_text(json.dumps(body))
    with pytest.raises(StorageError,match='identity|step|descriptor'):
        StageStore.collect_orphans(path)
    assert (path/second).read_bytes()==b'second'
