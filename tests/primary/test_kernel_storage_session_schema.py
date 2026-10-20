"""Session 每步重建复用本 writer 已验证的表集合；新 writer 重新验证。"""
import json

import apsw
import pytest

from society0.kernel import storage
from society0.kernel.storage import StageStore, StorageError


@pytest.mark.parametrize('schema,table', [
    ([], None),
    (['CREATE TABLE "quoted""name"(id TEXT PRIMARY KEY NOT NULL,value INTEGER) WITHOUT ROWID'], '"quoted""name"'),
])
def test_complete_reuses_validated_tables_but_reopen_validates_again(tmp_path, monkeypatch, schema, table):
    path = tmp_path / 'run'
    with StageStore.create(path, schema):
        pass
    validated = []
    original = storage._tables

    def tables(connection):
        result = original(connection)
        validated.append(tuple(result))
        return result

    monkeypatch.setattr(storage, '_tables', tables)
    with StageStore.open(path) as store:
        assert len(validated) == 1
        navigation = []
        store._connection.set_exec_trace(lambda cursor, sql, bindings: navigation.append(sql) or True)
        for step in range(1, 4):
            if table:
                store.transaction(lambda w: w.execute(f'INSERT INTO {table} VALUES(?,?)', (str(step), step)))
            store.complete(step)
        assert len(validated) == 1, 'complete revisited validated schema'
        assert not any('sqlite_schema' in sql or sql.startswith('PRAGMA table_info(') for sql in navigation)
    with StageStore.open(path) as reopened:
        assert len(validated) == 2
        reopened.complete(4)
        assert len(validated) == 2
    with StageStore.restore(path, tmp_path / 'restored') as restored:
        assert len(validated) == 3
        restored.complete(5)
        assert len(validated) == 3
        if table:
            assert restored.read(lambda r: r.query(f'SELECT value FROM {table} ORDER BY value')) == [(1,), (2,), (3,)]


def test_table_sets_belong_to_each_writer_and_capture_after_restore(tmp_path):
    paths = [tmp_path / name for name in ('first', 'second')]
    with StageStore.create(paths[0], ['CREATE TABLE first(id INTEGER PRIMARY KEY)']) as first, \
         StageStore.create(paths[1], ['CREATE TABLE second(id INTEGER PRIMARY KEY)']) as second:
        for step in range(1, 4):
            for store, table in ((first, 'first'), (second, 'second')):
                store.transaction(lambda w: w.execute(f'INSERT INTO {table} VALUES(?)', (step,)))
                store.complete(step)
    for path, table in zip(paths, ('first', 'second')):
        with StageStore.restore(path, tmp_path / (table + '-restored')) as restored:
            restored.transaction(lambda w: w.execute(f'INSERT INTO {table} VALUES(4)'))
            restored.complete(4)
        with StageStore.restore(restored.path, tmp_path / (table + '-continued')) as continued:
            assert continued.read(lambda r: r.query(f'SELECT id FROM {table} ORDER BY id')) == [(1,), (2,), (3,), (4,)]


def test_open_configures_connection_before_session_schema_navigation(tmp_path, monkeypatch):
    path = tmp_path / 'run'
    with StageStore.create(path, ['CREATE TABLE records(id INTEGER PRIMARY KEY)']):
        pass
    original = storage._tables
    calls = []

    def tables(connection):
        # foreign_keys 切换会使已有 prepared statements 失效；保留原有配置后导航顺序。
        assert connection.pragma('foreign_keys') == 1, 'configure connection before Session schema navigation'
        assert connection.pragma('journal_mode') == 'wal'
        assert connection.pragma('synchronous') == 2
        calls.append(1)
        return original(connection)

    monkeypatch.setattr(storage, '_tables', tables)
    with StageStore.open(path) as store:
        store.complete(1)
        assert calls == [1]


@pytest.mark.parametrize('operation', ['open', 'restore'])
@pytest.mark.parametrize('definition', ['id TEXT PRIMARY KEY', 'id TEXT'])
def test_new_writer_still_checks_primary_keys_and_releases_failed_open(tmp_path, operation, definition):
    path = tmp_path / 'run'
    with StageStore.create(path, ['CREATE TABLE records(id INTEGER PRIMARY KEY)']):
        pass
    # 令冻结定义与实际 schema 一致，单独检验主键验证，避免 schema 比较提前拒绝。
    for filename in ('current.sqlite', 'root.sqlite'):
        connection = apsw.Connection(str(path / filename))
        connection.execute('DROP TABLE records')
        connection.execute(f'CREATE TABLE records({definition})')
        definitions = storage._schema(connection)
        connection.close()
    manifest = json.loads((path / 'run.json').read_text())
    manifest['schema'] = definitions
    (path / 'run.json').write_text(json.dumps(manifest))
    if operation == 'restore':
        with pytest.raises(StorageError, match='primary key'):
            StageStore.restore(path, tmp_path / 'invalid-restored')
        path = tmp_path / 'invalid-restored'
        assert not list(tmp_path.glob('invalid-restored.building-*'))
    for _ in range(2):
        with pytest.raises(StorageError, match='primary key'):
            StageStore.open(path)
