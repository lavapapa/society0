"""APSW Session 与标准 zstd 文件流直接对接的最小可复现证明。"""
import json, pathlib, tempfile, resource, time
import apsw
from backports import zstd

with tempfile.TemporaryDirectory(prefix='society0-session-selection-') as d:
    path=pathlib.Path(d);source=apsw.Connection(str(path/'current.sqlite'))
    source.execute('CREATE TABLE facts(id INTEGER PRIMARY KEY,body TEXT)')
    with apsw.Connection(str(path/'root.sqlite')) as root:
        with root.backup('main',source,'main') as b:b.step(-1)
    session=apsw.Session(source,'main');session.attach('facts')
    for part in range(2):
        with source:
            source.executemany('INSERT INTO facts VALUES(?,?)',[(part*100+i,'原文🙂'+str(i)+'x'*4096) for i in range(100)])
    before=session.memory_used;started=time.perf_counter()
    with zstd.ZstdFile(path/'step1.zst','wb',options={zstd.CompressionParameter.compression_level:3,zstd.CompressionParameter.nb_workers:2}) as f:
        session.changeset_stream(f.write)
    session.close()
    # 完整点后的current污染不会进入从root+已封存stream重建的数据库。
    source.execute('INSERT INTO facts VALUES(1000,?)',('未完成步骤',))
    target=apsw.Connection(str(path/'restored.sqlite'))
    with apsw.Connection(str(path/'root.sqlite')) as root:
        with target.backup('main',root,'main') as b:b.step(-1)
    with zstd.ZstdFile(path/'step1.zst','rb') as f:apsw.Changeset.apply(f.read,target)
    assert list(target.execute('SELECT * FROM facts ORDER BY id'))==list(source.execute('SELECT * FROM facts WHERE id<1000 ORDER BY id'))
    assert target.execute('SELECT COUNT(*) FROM facts').get==200
    result={'rows':200,'transactions':2,'capture_memory_bytes':before,'compressed_changeset_bytes':(path/'step1.zst').stat().st_size,'stream_roundtrip_wall_s':time.perf_counter()-started,'full_rows_equal':True,'partial_step_excluded':True,'scope':'原生流对接与完整选定stream证明；未重新实现或验收多文件耐久协议'}
    target.close();source.close();print(json.dumps(result,ensure_ascii=False,indent=2))
