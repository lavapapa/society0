"""非存储作者编写的 v3 字典边界与发布失败复验。"""
import json
import random
import sqlite3

import pytest

from society0 import checkpoint_records as records


def test_v3_fences_preserve_typed_paths_duplicate_counts_and_write_order(tmp_path, monkeypatch):
    monkeypatch.setattr(records, 'KEY_BLOCK_BYTES', 160)
    paths = [[prefix, leaf] for prefix in [True, 1, '1', None, '中文']
             for leaf in [True, 1, '1', None, 'a\x00b', '😀', '', 'a' * 200, 'z', '09', 9, 10]]
    random.Random(19).shuffle(paths)
    operations = [{'path': path, 'operation': 'set', 'value': {'index': index}}
                  for index, path in enumerate(paths * 3)]
    file = tmp_path / 'rows.sqlite'
    records.write_records(file, operations)
    expected = [{**row, 'sequence': index} for index, row in enumerate(operations)]
    assert list(records.iter_records(file)) == expected
    metadata = list(records.iter_metadata(file))
    assert [json.dumps(row['path']) for row in metadata] == [json.dumps(row['path']) for row in expected]
    assert [row['sequence'] for row in metadata] == list(range(len(expected)))
    with sqlite3.connect(file) as db:
        assert db.execute('SELECT count(*) FROM key_blocks').fetchone()[0] > 10
    for path in paths:
        wanted = [row for row in expected if json.dumps(row['path']) == json.dumps(path)]
        first = records.read_page(file, path_filter=path, limit=2)
        assert first['total'] == 3
        second = records.read_page(file, path_filter=path, after_sequence=first['next_sequence'])
        assert first['records'] + second['records'] == wanted
        assert second['next_sequence'] is None
    for path in [['absent', 'a'], [True, 'not-present'], ['中文', 'zzzz']]:
        assert records.read_page(file, path_filter=path)['total'] == 0


def test_v3_merge_pending_and_published_keeps_global_sequence_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(records, 'KEY_BLOCK_BYTES', 128)
    expected, sources = [], []
    for batch, pending in enumerate([True, False, True, False]):
        source = tmp_path / f'{batch}.sqlite'
        rows = [{'sequence': batch * 100 + index, 'path': ['state', str(index % 7)],
                 'operation': 'set', 'value': {'batch': batch, 'index': index}} for index in range(20)]
        records.write_records(source, rows, pending=pending)
        expected.extend(rows)
        sources.append(source)
    target = tmp_path / 'merged.sqlite'
    records.merge_records(target, sources)
    assert list(records.iter_records(target)) == expected
    assert [row['sequence'] for row in records.iter_metadata(target)] == [row['sequence'] for row in expected]
    assert records.next_sequence(target) == 320
    for key in range(7):
        page = records.read_page(target, path_filter=['state', str(key)])
        assert page['records'] == [row for row in expected if row['path'][-1] == str(key)]
        assert page['total'] == len(page['records'])


def test_v3_index_failure_closes_database_and_keeps_published_file(tmp_path, monkeypatch):
    target = tmp_path / 'published.sqlite'
    old = [{'path': ['old'], 'operation': 'set', 'value': 7}]
    records.write_records(target, old)
    source = tmp_path / 'pending.sqlite'
    records.write_records(source, [{'path': ['new'], 'operation': 'set', 'value': 9}], pending=True)
    connections = []
    original = records.sqlite3.connect
    def tracked(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection
    def fail(db):
        raise OSError('injected dictionary publication failure')
    with monkeypatch.context() as patch:
        patch.setattr(records.sqlite3, 'connect', tracked)
        patch.setattr(records, '_finish_index', fail)
        with pytest.raises(OSError, match='dictionary publication'):
            records.merge_records(target, [source])
    assert list(records.iter_records(target)) == [{**old[0], 'sequence': 0}]
    assert not list(tmp_path.glob('.published.sqlite*'))
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            connection.execute('SELECT 1')


def test_v3_metadata_and_page_session_release_bounded_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(records, 'CHUNK', 256)
    file = tmp_path / 'cache.sqlite'
    rows = [{'path': ['records', str(i)], 'operation': 'map_create', 'id': i,
             'value': {'body': '汉' * 500, 'i': i}} for i in range(40)]
    records.write_records(file, rows)
    with monkeypatch.context() as patch:
        def forbid(*args, **kwargs):
            raise AssertionError('metadata must not read body')
        patch.setattr(records, '_record_bytes', forbid)
        metadata = list(records.iter_metadata(file, after_sequence=18))
    assert [row['id'] for row in metadata] == list(range(19, 40))
    with records.RecordReader(file) as reader:
        order = list(range(40)) * 3
        random.Random(7).shuffle(order)
        for sequence in order:
            size = reader.raw_bytes(sequence)
            tail = b''.join(reader.iter_bytes(sequence, offset=size-17, max_bytes=17))
            full = b''.join(records.iter_record_bytes(file, sequence))
            assert tail == full[-17:]
            assert reader.cached_bytes <= records.CHUNK
    assert reader.cached_bytes == 0
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        reader.db.execute('SELECT 1')


def test_v3_random_long_key_metadata_decodes_linear_blocks_and_seeks_late_pages(tmp_path, monkeypatch):
    from itertools import islice
    monkeypatch.setattr(records, 'KEY_BLOCK_BYTES', 4096)
    original = records.gzip.decompress
    measured = []
    for count in (250, 2500):
        order = list(range(count))
        random.Random(41).shuffle(order)
        file = tmp_path / f'linear-{count}.sqlite'
        rows = [{'path': ['items', f'{number:08d}-' + 'x' * 500], 'operation': 'set', 'value': number}
                for number in order]
        records.write_records(file, rows)
        with sqlite3.connect(file) as db:
            expected_blocks = db.execute('SELECT count(*) FROM metadata_keys').fetchone()[0]
        sizes = []
        def decode(payload):
            result = original(payload)
            sizes.append(len(result))
            return result
        def forbid_sorted_dictionary(*args, **kwargs):
            raise AssertionError('sequential metadata must not revisit sorted key blocks')
        with monkeypatch.context() as patch:
            patch.setattr(records.gzip, 'decompress', decode)
            patch.setattr(records, '_decode_keys', forbid_sorted_dictionary)
            assert [row['path'] for row in records.iter_metadata(file)] == [row['path'] for row in rows]
            assert len(sizes) == expected_blocks
            measured.append((len(sizes), sum(sizes)))
            sizes.clear()
            iterator = records.iter_metadata(file, after_sequence=count-4)
            try:
                late = list(islice(iterator, 3))
            finally:
                iterator.close()
            assert [row['sequence'] for row in late] == list(range(count-3, count))
            assert 1 <= len(sizes) <= 2
            assert sum(sizes) <= 2 * records.KEY_BLOCK_BYTES
            sizes.clear()
            assert list(records.iter_metadata(file, after_sequence=count+10)) == []
            assert sizes == []
    assert measured[1][0] <= measured[0][0] * 10 + 1
    assert measured[1][1] <= measured[0][1] * 10
