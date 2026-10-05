import json
import pytest
from society0.kernel.storage import StageStore,StorageError


def test_native_push_preserves_values_and_scope(tmp_path):
    from society0.kernel._json_chunks import write_json
    value={'integer':2**200,'float':1.2345678901234567,'text':'汉🙂\\"\x1b'*50000,'order':[False,None,1]}
    chunks=[];write_json(value,chunks.append)
    assert max(map(len,chunks))<=65536 and json.loads(b''.join(chunks))==value
    with StageStore.create(tmp_path/'run',['CREATE TABLE x(id INTEGER PRIMARY KEY)']) as store:
        compressed=[]
        store.transaction(lambda writer:writer.write_json_chunks(value,lambda size,body:compressed.append((size,body))))
        from society0.kernel._json_chunks import decode_chunk
        assert json.loads(b''.join(decode_chunk(body) for _,body in compressed))==value
        callback=store.transaction(lambda writer:writer.write_json_chunks)
        with pytest.raises(StorageError,match='scope'):callback({},lambda *a:None)


def test_emit_failure_and_invalid_value_preserve_transaction(tmp_path):
    with StageStore.create(tmp_path/'run',['CREATE TABLE x(id INTEGER PRIMARY KEY)']) as store:
        def change(writer):
            def emit(size,body):
                writer.execute('INSERT INTO x VALUES(1)')
                raise OSError('sink failed')
            writer.write_json_chunks({'body':'x'*1000000},emit)
        with pytest.raises(OSError,match='sink'):store.transaction(change)
        assert store.read(lambda r:r.query('SELECT * FROM x'))==[]


@pytest.mark.parametrize('error',[OSError('write failed'),KeyboardInterrupt('cancelled')])
def test_sink_preserves_first_failure_and_stops_effects(error):
    from society0.kernel._json_chunks import write_json
    calls=[]
    def sink(body):
        calls.append(len(body))
        raise error
    with pytest.raises(type(error)) as caught:
        write_json({'body':'汉🙂'*1000000},sink)
    assert caught.value is error
    assert len(calls)==1


@pytest.mark.parametrize('value',[{1:'bad'},float('nan'),float('inf'),(1,2),b'bytes'])
def test_native_push_rejects_non_json_values(value):
    from society0.kernel._json_chunks import write_json
    with pytest.raises((TypeError,ValueError)):
        write_json(value,lambda body:None)
