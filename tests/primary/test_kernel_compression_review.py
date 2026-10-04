"""原生同步编码的取消、定长帧及失败后恢复。"""
import pytest
from society0.kernel import _json_chunks as codec
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore


def test_review_partial_encoding_base_exception_rolls_back(tmp_path):
    emitted=[]
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        def write(writer):
            writer.execute('INSERT INTO item VALUES(1)')
            def emit(size,body):
                emitted.append(body)
                raise KeyboardInterrupt('simulated cancellation')
            writer.write_json_chunks({'body':'🙂'*400000},emit)
        with pytest.raises(KeyboardInterrupt):store.transaction(write)
        assert len(emitted)==1
        assert store.read(lambda r:r.query('SELECT * FROM item'))==[]
        store.transaction(lambda w:w.execute('INSERT INTO item VALUES(2)'))


def test_review_chunk_encoder_is_bounded_and_recovered_body_is_exact(tmp_path,monkeypatch):
    generated=0; native=codec.write_json
    def counted(value,emit):
        def consume(raw):
            nonlocal generated
            generated+=1;emit(raw)
        native(value,consume)
    monkeypatch.setattr(codec,'write_json',counted)
    value={'role':'user','content':'多字节🙂\\\"\n'*100000}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        first=[]
        def write(writer):
            def emit(size,body):
                first.append((size,body))
                assert generated==1
                raise InterruptedError('stop after first block')
            writer.write_json_chunks(value,emit)
        with pytest.raises(InterruptedError):store.transaction(write)
        size,chunk=first[0]
        assert size==len(codec.decode_chunk(chunk))==65536
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,value);store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restore') as restored:
        assert ThreadStore(restored).read_messages(tid)==[value]


def test_review_empty_changesets_are_valid_native_frames_and_restore(tmp_path):
    from backports import zstd
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        for step in range(1,5):
            if step in (1,4):store.transaction(lambda w:w.execute('INSERT INTO item VALUES(?)',(step,)))
            marker=store.complete(step)
            body=(store.path/marker['changeset']).read_bytes()
            assert body
            decoded=zstd.decompress(body)
            if step in (2,3):assert decoded==b''
            else:assert decoded
    with StageStore.restore(tmp_path/'run',tmp_path/'restore') as restored:
        assert restored.complete_step==4
        assert restored.read(lambda r:r.query('SELECT id FROM item ORDER BY id'))==[(1,),(4,)]
