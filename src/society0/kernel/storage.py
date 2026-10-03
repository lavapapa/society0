"""单写入器、短事务与 SQLite Session 完整步骤存储。"""
from __future__ import annotations

import fcntl
import inspect
import json
import os
from pathlib import Path
import shutil
import threading
import uuid

import apsw


class StorageError(RuntimeError):
    pass


def _sync(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _json(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _schema(connection):
    return [list(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' AND name NOT GLOB '_stage_*' ORDER BY type,name")]


def _tables(connection):
    tables = []
    for kind, name, _, sql in _schema(connection):
        if kind == 'index':
            continue
        if kind != 'table' or 'CREATE VIRTUAL' in sql.upper():
            raise StorageError('schema requires ordinary tables')
        info = list(connection.execute('PRAGMA table_info(' + '"' + name.replace('"', '""') + '")'))
        primary = [row for row in info if row[5]]
        if not primary or any(not row[3] and not (len(primary) == 1 and row[2].upper() == 'INTEGER') for row in primary):
            raise StorageError('schema requires an explicit nonnull primary key')
        tables.append(name)
    return tables


def _backup(source, path):
    target = apsw.Connection(str(path))
    try:
        with target.backup('main', source, 'main') as backup:
            backup.step(-1)
    finally:
        target.close()
    _sync(path)


def _call(callback, scope):
    result = callback(scope)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError('storage callback must not return an awaitable')
    return result


class ReadView:
    def __init__(self, connection, revision=0, complete=0):
        self._connection = connection
        self._active = True
        self._owner_thread = threading.get_ident()
        self.live_revision = revision
        self.complete_step = complete

    def _check(self):
        if not self._active or threading.get_ident() != self._owner_thread:
            raise StorageError('callback scope expired or belongs to another thread')

    def read_blob(self, table, column, rowid, *, offset=0, size=65536):
        self._check()
        if type(rowid) is not int or type(offset) is not int or offset < 0 or type(size) is not int or size < 0:
            raise ValueError('invalid BLOB range')
        with self._connection.blob_open('main', table, column, rowid, False) as blob:
            total = blob.length()
            if offset >= total or size == 0:
                return b'', total
            blob.seek(offset)
            return blob.read(min(size, total-offset)), total

    def iter_query(self, sql, bindings=()):
        self._check()
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, bindings)
            while True:
                self._check()
                row = next(cursor, None)
                if row is None:
                    return
                yield row
        finally:
            cursor.close()

    def query(self, sql, bindings=(), *, max_rows=1000):
        self._check()
        if type(max_rows) is not int or max_rows < 0:
            raise ValueError('max_rows must be nonnegative')
        cursor = self._connection.cursor()
        try:
            cursor.execute(sql, bindings)
            rows = []
            for row in cursor:
                if len(rows) == max_rows:
                    raise StorageError('query row limit exceeded')
                rows.append(row)
            return rows
        finally:
            cursor.close()


class Writer(ReadView):
    def include_artifact(self, reference):
        self._check()
        self._include_artifact(reference)

    def execute(self, sql, bindings=()):
        self._check()
        cursor = self._connection.execute(sql, bindings)
        try:
            if next(cursor, None) is not None:
                raise StorageError('use query for returning rows')
        finally:
            cursor.close()

    def executemany(self, sql, bindings):
        self._check()
        cursor = self._connection.executemany(sql, bindings)
        try:
            if next(cursor, None) is not None:
                raise StorageError('use query for returning rows')
        finally:
            cursor.close()


_ALLOWED = {apsw.SQLITE_SELECT, apsw.SQLITE_READ, apsw.SQLITE_FUNCTION, apsw.SQLITE_RECURSIVE,
            apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}


def _authorizer(action, first, second, database, trigger):
    if action == apsw.SQLITE_PRAGMA and first in ('table_info', 'table_xinfo', 'table_list'):
        return apsw.SQLITE_OK
    if action not in _ALLOWED or (action in (apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE)
                                 and (first or '').startswith('_stage_')):
        return apsw.SQLITE_DENY
    return apsw.SQLITE_OK


class StageReader:
    """独立只读观察者；完成水位是写入器已确认的下界。"""
    def __init__(self, path):
        self.path = Path(path).absolute()

    def read_artifact(self, reference, *, offset=0, size=65536):
        if type(offset) is not int or offset < 0 or type(size) is not int or size < 0:
            raise ValueError('invalid artifact range')
        relative = Path(reference)
        file = self.path / relative
        if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'artifacts' or not file.resolve().is_relative_to(self.path):
            raise StorageError('artifact must be run-local')
        with file.open('rb') as stream:
            total = os.fstat(stream.fileno()).st_size
            stream.seek(offset)
            return stream.read(size), total

    def read(self, callback, *, expected_revision=None):
        connection = apsw.Connection(str(self.path / 'current.sqlite'), flags=apsw.SQLITE_OPEN_READONLY)
        scope = None
        try:
            connection.execute('BEGIN')
            revision, complete = connection.execute('SELECT revision,complete_step FROM _stage_runtime').get
            if expected_revision is not None and revision != expected_revision:
                raise StorageError('read revision changed')
            scope = ReadView(connection, revision, complete)
            connection.set_authorizer(_authorizer)
            return _call(callback, scope)
        finally:
            if scope:
                scope._active = False
            connection.close()


class StageStore:
    @classmethod
    def validate_schema(cls, path, schema):
        manifest = json.loads((Path(path) / 'run.json').read_text())
        connection = apsw.Connection(':memory:')
        try:
            for ddl in schema:
                connection.execute(ddl)
            _tables(connection)
            if _schema(connection) != manifest['schema']:
                raise StorageError('declared schema differs from source run')
        finally:
            connection.close()

    @classmethod
    def create(cls, path, schema, *, initialize=None, run_id=None):
        path = Path(path).absolute()
        temporary = path.with_name(path.name + '.building-' + uuid.uuid4().hex)
        temporary.mkdir(parents=True)
        connection = None
        try:
            connection = apsw.Connection(str(temporary / 'current.sqlite'))
            connection.execute('PRAGMA foreign_keys=ON')
            for ddl in schema:
                connection.execute(ddl)
            _tables(connection)
            definitions = _schema(connection)
            if initialize:
                scope = Writer(connection)
                connection.execute('BEGIN')
                connection.set_authorizer(_authorizer)
                try:
                    _call(initialize, scope)
                finally:
                    scope._active = False
                    connection.set_authorizer(None)
                connection.execute('COMMIT')
            identity = run_id or uuid.uuid4().hex
            connection.execute('CREATE TABLE _stage_runtime(id INTEGER PRIMARY KEY,run_id TEXT,revision INTEGER,failed INTEGER,complete_step INTEGER)')
            connection.execute('INSERT INTO _stage_runtime VALUES(1,?,0,0,0)', (identity,))
            connection.execute('CREATE TABLE _stage_artifacts(path TEXT PRIMARY KEY NOT NULL,size INTEGER NOT NULL,revision INTEGER NOT NULL)')
            connection.execute('CREATE INDEX _stage_artifact_revision ON _stage_artifacts(revision)')
            manifest = {'format': 1, 'run_id': identity, 'schema': definitions, 'root_step': 0, 'source': None}
            cls._finish_initial(temporary, connection, manifest, [])
            connection.close()
            connection = None
            if path.exists():
                raise FileExistsError(path)
            os.rename(temporary, path)
            _sync(path.parent)
            return cls.open(path)
        finally:
            if connection:
                connection.close()
            shutil.rmtree(temporary, ignore_errors=True)

    @staticmethod
    def _finish_initial(path, connection, manifest, artifacts):
        (path / 'steps').mkdir()
        (path / 'changesets').mkdir()
        _backup(connection, path / 'root.sqlite')
        _json(path / 'run.json', manifest)
        step = manifest['root_step']
        _json(path / 'steps' / f'{step:020d}.json', {'run_id': manifest['run_id'], 'step': step,
              'parent': None, 'live_revision': 0, 'changeset': None, 'artifacts': artifacts, 'requested_artifacts': []})
        _sync(path)

    @classmethod
    def open(cls, path):
        self = cls.__new__(cls)
        self.path = Path(path).absolute()
        self._manifest = json.loads((self.path / 'run.json').read_text())
        self.run_id = self._manifest['run_id']
        self.source = self._manifest['source']
        self._last = self._latest(self.path)
        self._lock = (self.path / 'writer.lock').open('a+b')
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._lock.close()
            raise StorageError('another writer owns this run') from error
        self._connection = apsw.Connection(str(self.path / 'current.sqlite'))
        self._closed = False
        self._busy = False
        self._failed = False
        self._thread = threading.get_ident()
        try:
            row = self._connection.execute('SELECT run_id,revision,failed FROM _stage_runtime').get
            if row != (self.run_id, self._last['live_revision'], 0):
                raise StorageError('current contains an incomplete step; restore required')
            if _schema(self._connection) != self._manifest['schema']:
                raise StorageError('schema mismatch')
            self._validate_references(self.path, self._manifest, self._last['step'])
            self._connection.execute('PRAGMA foreign_keys=ON')
            self._connection.execute('PRAGMA journal_mode=WAL')
            self._connection.execute('PRAGMA synchronous=FULL')
            self._connection.execute('UPDATE _stage_runtime SET complete_step=?', (self.complete_step,))
            self._start_session()
            return self
        except BaseException:
            self._connection.close()
            self._lock.close()
            raise

    @staticmethod
    def _latest(path):
        markers = (path / 'steps').glob('*.json')
        latest = max(markers, default=None)
        if latest is None:
            raise StorageError('missing complete descriptor')
        return json.loads(latest.read_text())

    @staticmethod
    def _validate_references(path, manifest, selected):
        for number in range(manifest['root_step'], selected + 1):
            marker = path / 'steps' / f'{number:020d}.json'
            if not marker.is_file():
                raise StorageError('missing complete descriptor')
            item = json.loads(marker.read_text())
            parent = None if number == manifest['root_step'] else number - 1
            if (item['run_id'], item['step'], item['parent']) != (manifest['run_id'], number, parent):
                raise StorageError('complete chain identity mismatch')
            if item['changeset'] and not (path / item['changeset']).is_file():
                raise StorageError('missing changeset')
            for artifact in item['artifacts']:
                file = path / artifact['path']
                if not file.is_file() or file.stat().st_size != artifact['size']:
                    raise StorageError('missing or changed artifact')

    def _start_session(self):
        self._session = apsw.Session(self._connection, 'main')
        for table in _tables(self._connection):
            self._session.attach(table)

    @property
    def complete_step(self):
        return self._last['step']

    def _check(self):
        if self._closed or self._failed:
            raise StorageError('store unavailable; restore required')
        if threading.get_ident() != self._thread or self._busy:
            raise StorageError('single writer callback scope required')

    def transaction(self, callback):
        self._check()
        self._busy = True
        scope = Writer(self._connection)
        scope._include_artifact = self._include_artifact
        try:
            self._connection.execute('BEGIN IMMEDIATE')
            self._connection.set_authorizer(_authorizer)
            result = _call(callback, scope)
            self._connection.set_authorizer(None)
            self._connection.execute('UPDATE _stage_runtime SET revision=revision+1')
            self._connection.execute('COMMIT')
            return result
        except BaseException:
            self._connection.set_authorizer(None)
            if not self._connection.get_autocommit():
                self._connection.execute('ROLLBACK')
            else:
                self._failed = True
            raise
        finally:
            scope._active = False
            self._connection.set_authorizer(None)
            self._busy = False

    def read(self, callback, *, expected_revision=None):
        self._check()
        return StageReader(self.path).read(callback, expected_revision=expected_revision)

    def read_artifact(self, reference, *, offset=0, size=65536):
        self._check()
        return StageReader(self.path).read_artifact(reference, offset=offset, size=size)

    def _include_artifact(self, reference):
        item = self._artifacts([reference])[0]
        self._connection.set_authorizer(None)
        try:
            self._connection.execute('INSERT OR IGNORE INTO _stage_artifacts SELECT ?,?,revision+1 FROM _stage_runtime',
                                     (item['path'],item['size']))
        finally:
            self._connection.set_authorizer(_authorizer)

    def prepare_artifact(self, chunks):
        """将字节流封存为独占的新文件；返回 run 内引用。"""
        self._check()
        directory = self.path / 'artifacts'
        directory.mkdir(exist_ok=True)
        temporary = directory / (uuid.uuid4().hex + '.tmp')
        final = temporary.with_suffix('.artifact')
        try:
            with temporary.open('xb') as stream:
                for chunk in chunks:
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, final)
            _sync(directory)
            _sync(self.path)
            return str(final.relative_to(self.path))
        finally:
            temporary.unlink(missing_ok=True)

    def abort_step(self):
        self._check()
        self._connection.execute('UPDATE _stage_runtime SET failed=1')
        self._failed = True

    def _artifacts(self, artifacts):
        result = []
        for name in sorted(set(artifacts)):
            relative = Path(name)
            path = self.path / relative
            if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] != 'artifacts':
                raise StorageError('artifact must be run-local under artifacts/')
            if not path.is_file() or not path.resolve().is_relative_to(self.path):
                raise StorageError('missing run-local artifact')
            _sync(path)
            _sync(path.parent)
            result.append({'path': str(relative), 'size': path.stat().st_size})
        return result

    def _fault(self, phase):
        """阶段边界供进程故障测试替换。"""

    def complete(self, step, *, artifacts=()):
        self._check()
        if type(step) is not int:
            raise StorageError('complete step must be an integer')
        revision = self._connection.execute('SELECT revision FROM _stage_runtime').get
        if step == self.complete_step:
            if revision == self._last['live_revision'] and sorted(set(artifacts)) == self._last['requested_artifacts']:
                return self._last.copy()
            raise StorageError('complete identity or artifacts differ')
        if type(step) is not int or step != self.complete_step + 1:
            raise StorageError('complete step must follow parent')
        temporary = self.path / 'changesets' / (uuid.uuid4().hex + '.tmp')
        try:
            requested = sorted(set(artifacts))
            included = [row[0] for row in self._connection.execute('SELECT path FROM _stage_artifacts WHERE revision>? AND revision<=?',
                                                                 (self._last['live_revision'], revision))]
            references = self._artifacts([*requested,*included])
            final = temporary.with_suffix('.changeset')
            with temporary.open('xb') as stream:
                def output(data):
                    stream.write(data)
                self._session.changeset_stream(output)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final)
            _sync(final.parent)
            self._fault('before_publish')
            descriptor = {'run_id': self.run_id, 'step': step, 'parent': self.complete_step,
                          'live_revision': revision, 'changeset': str(final.relative_to(self.path)), 'artifacts': references, 'requested_artifacts': requested}
            _json(self.path / 'steps' / f'{step:020d}.json', descriptor)
            self._last = descriptor
            self._session.close()
            self._start_session()
            self._connection.execute('UPDATE _stage_runtime SET complete_step=?', (step,))
            self._fault('after_publish')
            return descriptor.copy()
        except BaseException:
            if self.complete_step != step:
                self._failed = True
            raise
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def restore(cls, source, destination, *, step=None, run_id=None):
        source, destination = Path(source).absolute(), Path(destination).absolute()
        manifest = json.loads((source / 'run.json').read_text())
        if run_id == manifest['run_id']:
            raise StorageError('restore requires a new run identity')
        selected = cls._latest(source)['step'] if step is None else step
        if type(selected) is not int or selected < manifest['root_step']:
            raise StorageError('invalid recovery step')
        temporary = destination.with_name(destination.name + '.building-' + uuid.uuid4().hex)
        temporary.mkdir(parents=True)
        connection = original = None
        try:
            original = apsw.Connection(str(source / 'root.sqlite'), flags=apsw.SQLITE_OPEN_READONLY)
            if _schema(original) != manifest['schema']:
                raise StorageError('root schema mismatch')
            _backup(original, temporary / 'current.sqlite')
            original.close()
            original = None
            connection = apsw.Connection(str(temporary / 'current.sqlite'))
            connection.execute('PRAGMA foreign_keys=ON')
            references = {}
            for number in range(manifest['root_step'], selected + 1):
                marker = source / 'steps' / f'{number:020d}.json'
                if not marker.is_file():
                    raise StorageError('missing complete descriptor')
                descriptor = json.loads(marker.read_text())
                parent = None if number == manifest['root_step'] else number - 1
                if descriptor['run_id'] != manifest['run_id'] or descriptor['step'] != number or descriptor['parent'] != parent:
                    raise StorageError('complete chain identity mismatch')
                if number != manifest['root_step']:
                    changeset = source / descriptor['changeset']
                    if not changeset.is_file():
                        raise StorageError('missing changeset')
                    with changeset.open('rb') as stream:
                        # 级联结果已在 changeset 内；避免应用时再次触发级联。
                        apsw.Changeset.apply(stream.read, connection, flags=apsw.SQLITE_CHANGESETAPPLY_FKNOACTION)
                for artifact in descriptor['artifacts']:
                    name = artifact['path']
                    source_file = source / name
                    if not source_file.is_file() or source_file.stat().st_size != artifact['size']:
                        raise StorageError('missing or changed artifact')
                    target_file = temporary / name
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source_file, target_file)
                    _sync(target_file)
                    _sync(target_file.parent)
                    references[name] = artifact
            connection.execute('DELETE FROM _stage_artifacts')
            connection.executemany('INSERT INTO _stage_artifacts VALUES(?,?,0)', ((item['path'],item['size']) for item in references.values()))
            identity = run_id or uuid.uuid4().hex
            connection.execute('UPDATE _stage_runtime SET run_id=?,revision=0,failed=0,complete_step=?', (identity, selected))
            fresh = dict(manifest, run_id=identity, root_step=selected, source={'run_id': manifest['run_id'], 'step': selected})
            cls._finish_initial(temporary, connection, fresh, list(references.values()))
            connection.close()
            connection = None
            if destination.exists():
                raise FileExistsError(destination)
            os.rename(temporary, destination)
            _sync(destination.parent)
            return cls.open(destination)
        finally:
            if original:
                original.close()
            if connection:
                connection.close()
            shutil.rmtree(temporary, ignore_errors=True)

    fork = restore

    def close(self):
        if not self._closed:
            self._session.close()
            self._connection.close()
            self._lock.close()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
