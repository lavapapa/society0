import pytest

@pytest.mark.asyncio
async def test_native_reader_handles_utf8_long_lines_and_document_boundaries():
    from society0.kernel.information_fs import search_reader
    for body, expected in [(b'x'*65533+'关键词'.encode()+b'\n',1), ('关键'.encode(),0),('词'.encode(),0),('关键词'.encode(),1)]:
        hits=[]
        async def read(offset,size):return body[offset:offset+min(size,2)]
        async def sink(line,offset,data):hits.append((line,offset,data))
        result=await search_reader(read,sink,'关(?:键)词')
        assert result['matches']==expected
        assert len(hits)==expected
        if hits:assert hits[0][0:2]==(1,0)

@pytest.mark.asyncio
async def test_native_reader_rejects_multiline_and_propagates_callback_errors():
    from society0.kernel.information_fs import search_reader
    async def read(offset,size):raise ValueError('source revision changed')
    async def sink(*args):pass
    with pytest.raises(Exception,match='source revision changed'):
        await search_reader(read,sink,'x')
    with pytest.raises(Exception):await search_reader(read,sink,'a\\nb')


@pytest.mark.asyncio
async def test_native_cancel_drains_callbacks_before_return():
    import asyncio
    from society0.kernel.information_fs import search_reader
    entered=asyncio.Event();finished=asyncio.Event();hits=[]
    async def read(offset,size):
        entered.set()
        try:await asyncio.Event().wait()
        finally:finished.set()
    async def sink(*args):hits.append(args)
    task=asyncio.create_task(search_reader(read,sink,'x'))
    await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert finished.is_set() and not hits


@pytest.mark.asyncio
async def test_dense_native_transport_batches_preserve_every_original_hit():
    from society0.kernel.information_fs import search_reader
    body=b'needle full original\n'*5000;hits=[]
    async def read(offset,size):return body[offset:offset+size]
    async def sink(line,offset,data):hits.append((line,offset,data))
    result=await search_reader(read,sink,'needle',literal=True)
    assert result['matches']==5000
    assert hits==[(i+1,i*len(b'needle full original\n'),b'needle full original\n') for i in range(5000)]
    assert result['sink_batches']<20
