"""生命周期非作者复验：选定链、孤儿删除前的完整身份。"""
import json
import shutil
import pytest
from society0.kernel.storage import StageStore,StorageError


def test_review_selected_export_excludes_future_dependencies_and_reopens(tmp_path):
    schema=['CREATE TABLE item(id INTEGER PRIMARY KEY,n TEXT NOT NULL)']
    with StageStore.create(tmp_path/'source',schema) as store:
        first=store.prepare_artifact([b'first'])
        store.transaction(lambda w:w.execute("INSERT INTO item VALUES(1,'first')"))
        store.complete(1,artifacts=[first])
        future=store.prepare_artifact([b'future'])
        store.transaction(lambda w:w.execute("UPDATE item SET n='future'"))
        store.complete(2,artifacts=[first,future])
    with StageStore.restore(tmp_path/'source',tmp_path/'selected',step=1) as selected:
        assert selected.read(lambda r:r.query('SELECT n FROM item'))==[('first',)]
        assert (selected.path/first).read_bytes()==b'first'
        assert not (selected.path/future).exists()
    shutil.rmtree(tmp_path/'source')
    with StageStore.open(tmp_path/'selected') as selected:
        selected.transaction(lambda w:w.execute("UPDATE item SET n='selected branch'"))
        selected.complete(2)
    with StageStore.restore(tmp_path/'selected',tmp_path/'again') as restored:
        assert restored.read(lambda r:r.query('SELECT n FROM item'))==[('selected branch',)]
        assert (restored.path/first).read_bytes()==b'first'


@pytest.mark.parametrize('corruption',['wrong_latest_step','missing_nonroot_changeset'])
def test_review_gc_validates_descriptor_filename_and_required_changeset_before_delete(tmp_path,corruption):
    path=tmp_path/'run'
    with StageStore.create(path,['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        store.transaction(lambda w:w.execute('INSERT INTO item VALUES(1)'))
        descriptor=store.complete(1)
        orphan=store.prepare_artifact([b'diagnostic'])
    marker=path/'steps'/'00000000000000000001.json'
    bad=json.loads(marker.read_text())
    if corruption=='wrong_latest_step':bad['step']=0
    else:bad['changeset']=None
    marker.write_text(json.dumps(bad))
    with pytest.raises(StorageError):StageStore.collect_orphans(path)
    assert (path/orphan).exists()
    assert (path/descriptor['changeset']).exists()
