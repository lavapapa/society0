"""存储作者对组合初始化和恢复生命周期的独立审查。"""
import pytest
from society0.kernel.composition import compose
from society0.kernel.plugins import Plugin
from society0.kernel.storage import StageStore,StorageError


@pytest.mark.asyncio
async def test_review_initializer_failure_publishes_no_partial_run(tmp_path):
    def first(w):w.execute('INSERT INTO facts VALUES(1)')
    def second(w):
        assert w.query('SELECT * FROM facts')==[(1,)]
        raise ValueError('second initializer failed')
    with pytest.raises(ValueError,match='second initializer'):
        async with compose(tmp_path/'run',[
            Plugin('first',schema=('CREATE TABLE facts(id INTEGER PRIMARY KEY)',),initialize=first),
            Plugin('second',requires=('first',),initialize=second)]):pass
    assert not (tmp_path/'run').exists()


@pytest.mark.asyncio
async def test_review_restore_preflight_ignores_dirty_current_and_never_initializes(tmp_path):
    schema=('CREATE TABLE facts(id INTEGER PRIMARY KEY)',)
    with StageStore.create(tmp_path/'source',schema,initialize=lambda w:w.execute('INSERT INTO facts VALUES(1)')) as store:
        store.transaction(lambda w:w.execute('INSERT INTO facts VALUES(2)'))
        def forbidden(w):raise AssertionError('initializer reran')
        async with compose(tmp_path/'restore',[Plugin('facts',schema=schema,initialize=forbidden)],source=store.path) as host:
            restored=host.service('storage','store')
            assert restored.read(lambda r:r.query('SELECT * FROM facts'))==[(1,)]
        with pytest.raises(StorageError,match='schema'):
            async with compose(tmp_path/'bad',[Plugin('facts',schema=(*schema,'CREATE TABLE extra(id INTEGER PRIMARY KEY)'))],source=store.path):pass
        assert not (tmp_path/'bad').exists()


@pytest.mark.asyncio
async def test_review_plugin_cleanup_can_read_store_before_close(tmp_path):
    seen=[]
    def install(context):
        store=context.require('storage','store')
        context.on_close(lambda:seen.extend(store.read(lambda r:r.query('SELECT * FROM facts'))))
    async with compose(tmp_path/'run',[Plugin('facts',requires=('storage',),schema=('CREATE TABLE facts(id INTEGER PRIMARY KEY)',),
                                             initialize=lambda w:w.execute('INSERT INTO facts VALUES(7)'),install=install)]):pass
    assert seen==[(7,)]
    with StageStore.open(tmp_path/'run'):pass
