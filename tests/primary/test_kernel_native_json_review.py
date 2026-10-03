"""非作者复验原生 sink 的错误优先级与 writer 线程边界。"""
import json
import threading
import pytest
from society0.kernel._json_chunks import write_json
from society0.kernel.storage import StageStore


def test_review_sink_failure_remains_primary_when_later_value_is_invalid():
    expected=OSError('original write error');calls=[]
    def emit(raw):
        calls.append(raw)
        raise expected
    with pytest.raises(OSError) as caught:
        write_json({'first':'原文'*100000,'later':object()},emit)
    assert caught.value is expected and len(calls)==1


def test_review_native_pipeline_emits_only_on_writer_and_restores_full_unicode(tmp_path,monkeypatch):
    import society0.kernel._json_chunks as module
    owner=threading.get_ident();workers=[];emissions=[]
    original=module.zlib.compress
    def compress(*args):
        workers.append(threading.get_ident())
        return original(*args)
    monkeypatch.setattr(module.zlib,'compress',compress)
    value={'text':'汉字🙂\\\"\n'*100000,'large_integer':2**200,'nested':[True,None,-0.0]}
    with StageStore.create(tmp_path/'run',['CREATE TABLE blocks(id INTEGER PRIMARY KEY,raw_bytes INTEGER,payload BLOB)']) as store:
        def write(writer):
            def emit(size,body):
                emissions.append(threading.get_ident())
                writer.execute('INSERT INTO blocks VALUES(?,?,?)',(len(emissions),size,body))
            writer.write_json_chunks(value,emit)
        store.transaction(write)
        assert workers and all(thread!=owner for thread in workers)
        assert emissions and all(thread==owner for thread in emissions)
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        blocks=store.read(lambda view:view.query('SELECT payload FROM blocks ORDER BY id'))
        assert json.loads(b''.join(module.zlib.decompress(body) for body, in blocks))==value
