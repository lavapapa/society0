"""异步外部准备与根事务分离，资源和巨大准备值及时释放。"""
import asyncio
from contextlib import asynccontextmanager
import gc
import weakref
import pytest
from society0.kernel.plugins import Plugin
from society0.kernel.composition import compose
from society0.kernel.storage import StageStore

SCHEMA=('CREATE TABLE facts(id INTEGER PRIMARY KEY,value TEXT NOT NULL)',)


@pytest.mark.asyncio
async def test_prepare_value_released_before_running_and_restore_skips_loader(tmp_path):
    calls=[];references=[]
    class Prepared:
        def __init__(self):self.text='full prepared body'*10000
    @asynccontextmanager
    async def prepare():
        calls.append('load');value=Prepared();references.append(weakref.ref(value))
        await asyncio.sleep(0)
        try:yield lambda writer:writer.execute('INSERT INTO facts VALUES(1,?)',(value.text,))
        finally:calls.append('closed')
    plugin=Plugin('facts',schema=SCHEMA,prepare=prepare)
    async with compose(tmp_path/'run',[plugin]) as host:
        gc.collect();assert references[0]() is None and calls==['load','closed']
        assert host.service('storage','store').read(lambda r:r.query('SELECT length(value) FROM facts'))==[(len('full prepared body'*10000),)]
    async with compose(tmp_path/'restored',[plugin],source=tmp_path/'run'):
        assert calls==['load','closed']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['next_prepare','root_write','cancel'])
async def test_prepare_failures_release_resources_without_partial_root(tmp_path,failure):
    events=[];entered=asyncio.Event()
    @asynccontextmanager
    async def first():
        events.append('first open')
        try:yield lambda w:w.execute("INSERT INTO facts VALUES(1,'initial')")
        finally:events.append('first close')
    @asynccontextmanager
    async def second():
        events.append('second open')
        try:
            if failure=='next_prepare':raise ValueError('prepare failed')
            if failure=='cancel':entered.set();await asyncio.Event().wait()
            def write(writer):raise ValueError('root failed')
            yield write
        finally:events.append('second close')
    plugins=[Plugin('one',schema=SCHEMA,prepare=first),Plugin('two',('one',),prepare=second)]
    async def execute():
        async with compose(tmp_path/'run',plugins):pass
    if failure=='cancel':
        task=asyncio.create_task(execute());await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    else:
        with pytest.raises(ValueError):await execute()
    assert events==['first open','second open','second close','first close']
    assert not (tmp_path/'run').exists()


@pytest.mark.asyncio
async def test_prepare_cleanup_failure_closes_published_store(tmp_path):
    @asynccontextmanager
    async def prepare():
        try:yield lambda w:w.execute("INSERT INTO facts VALUES(1,'valid root')")
        finally:raise RuntimeError('cleanup failed')
    with pytest.raises(RuntimeError,match='cleanup failed'):
        async with compose(tmp_path/'run',[Plugin('facts',schema=SCHEMA,prepare=prepare)]):pass
    with StageStore.open(tmp_path/'run') as store:
        assert store.complete_step==0
        assert store.read(lambda r:r.query('SELECT value FROM facts'))==[('valid root',)]
