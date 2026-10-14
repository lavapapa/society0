"""APSW 显式关闭游标保留对象：规范作用域释放避免逐次查询泄漏。"""
import gc
import tracemalloc
import apsw
import pytest
from society0.kernel.storage import Writer, StorageError


@pytest.mark.parametrize('operation', ['query', 'iterate', 'execute', 'executemany', 'escaped'])
def test_completed_sql_operations_release_cursor_objects(operation):
    connection = apsw.Connection(':memory:')
    connection.execute('CREATE TABLE sample(id INTEGER PRIMARY KEY NOT NULL, value INTEGER)')
    connection.execute('INSERT INTO sample VALUES(1,0)')
    view = Writer(connection)
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for index in range(2000):
            if operation == 'query':
                assert view.query('SELECT value FROM sample') == [(0,)]
            elif operation == 'iterate':
                assert list(view.iter_query('SELECT value FROM sample')) == [(0,)]
            elif operation == 'execute':
                view.execute('UPDATE sample SET value=? WHERE id=1', (index,))
            elif operation == 'executemany':
                view.executemany('UPDATE sample SET value=? WHERE id=1', [(index,)])
            else:
                scope = Writer(connection)
                iterator = scope.iter_query('SELECT value FROM sample UNION ALL SELECT value FROM sample')
                assert next(iterator) == (0,)
                scope._finish()
                with pytest.raises(StorageError):
                    next(iterator)
                del scope, iterator
        gc.collect()
        retained = tracemalloc.get_traced_memory()[0] - before
        assert retained < 64000, (operation, retained)
    finally:
        tracemalloc.stop()
        view._finish()
        connection.close()
