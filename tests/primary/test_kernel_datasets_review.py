"""不可变批次存储的独立消费者与工作量边界。"""
import json
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.datasets import Datasets, DATASET_SCHEMA


def test_review_dataset_page_serializes_items_linearly(tmp_path,monkeypatch):
    import society0.kernel.datasets as module
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        data=Datasets(store);reference=data.import_rows('numbers',range(1000))
        real=module._bytes;items_encoded=0
        def counted(value):
            nonlocal items_encoded
            items_encoded+=len(value['items']) if isinstance(value,dict) and 'items' in value else 1
            return real(value)
        monkeypatch.setattr(module,'_bytes',counted)
        page=data.page(reference,limit=1000,max_bytes=1_000_000)
        assert len(page['items'])==1000 and page['next_cursor'] is None
        assert items_encoded<=2000, 'page metadata repeatedly serializes prior items'


def test_review_dataset_restore_changes_cursor_identity_and_readonly_rejects_import(tmp_path):
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        data=Datasets(store);reference=data.import_rows('numbers',[1,2,3])
        cursor=json.loads(json.dumps(data.page(reference,limit=1)['next_cursor']))
        store.complete(1)
        with StageStore.prepare_readonly(store.path,tmp_path/'view') as reader:
            restored=Datasets(reader)
            with pytest.raises(ValueError,match='cursor'):restored.page(reference,cursor=cursor)
            with pytest.raises((AttributeError,RuntimeError)):
                restored.import_rows('forbidden',[4])
            assert restored.page(reference)['total']==3


def test_review_dataset_large_range_and_final_page_budget(tmp_path,monkeypatch):
    import society0.kernel.datasets as module
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        data=Datasets(store);value={'text':'完整🙂'*500000}
        reference=data.import_rows('large',[value]*4)
        page=data.page(reference,max_bytes=512)
        assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())<=512
        assert page['next_cursor'] is not None and 'payload_ref' in page['items'][0]
        raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
        decompress=module.zlib.decompress;calls=[]
        monkeypatch.setattr(module.zlib,'decompress',lambda value:(calls.append(len(value)),decompress(value))[1])
        assert data.read_payload(reference,0,offset=len(raw)-17,size=17)['data']==raw[-17:]
        assert len(calls)==1
