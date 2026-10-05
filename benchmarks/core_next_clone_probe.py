"""在独立临时目录比较原生文件克隆与复制，验证关闭的 SQLite 根隔离。"""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
import sqlite3


def allocated(path):return path.stat().st_blocks*512


def probe(root,mebibytes):
    source=root/'immutable.sqlite'
    db=sqlite3.connect(str(source))
    db.execute('CREATE TABLE blocks(id INTEGER PRIMARY KEY,body BLOB NOT NULL)')
    with db:
        db.executemany('INSERT INTO blocks VALUES(?,?)',((index,os.urandom(65536)) for index in range(mebibytes*16)))
    db.close()
    with source.open('rb') as stream:os.fsync(stream.fileno())
    result={'platform':platform.platform(),'sqlite':sqlite3.sqlite_version,'filesystem_path':str(root),'source_bytes':source.stat().st_size,
            'source_allocated_bytes':allocated(source),'comparisons':[]}
    for method in ('copy','clone'):
        target=root/(method+'.sqlite')
        free_before=shutil.disk_usage(root).free
        start=time.perf_counter();cpu=time.process_time()
        if method=='clone':
            command=['cp','-c',str(source),str(target)] if platform.system()=='Darwin' else ['cp','--reflink=always',str(source),str(target)]
            completed=subprocess.run(command,capture_output=True,text=True)
            if completed.returncode:
                result['comparisons'].append({'method':method,'supported':False,'stderr':completed.stderr})
                continue
        else:shutil.copyfile(source,target)
        with target.open('rb') as stream:os.fsync(stream.fileno())
        item={'method':method,'supported':True,'wall_seconds':time.perf_counter()-start,
              'parent_cpu_seconds':time.process_time()-cpu,'logical_bytes':target.stat().st_size,
              'reported_allocated_bytes':allocated(target),
              'volume_free_delta_bytes':free_before-shutil.disk_usage(root).free}
        # 全文件逐块比较与单页修改隔离，不新增摘要或校验和。
        with source.open('rb') as left,target.open('rb') as right:
            while chunk:=left.read(65536):assert chunk==right.read(len(chunk))
            assert right.read(1)==b''
        original=sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True)
        copy=sqlite3.connect(str(target),isolation_level=None)
        before=original.execute('SELECT body FROM blocks WHERE id=0').fetchone()[0]
        copy.execute('UPDATE blocks SET body=? WHERE id=0',(b'changed',))
        assert original.execute('SELECT body FROM blocks WHERE id=0').fetchone()[0]==before
        assert copy.execute('SELECT body FROM blocks WHERE id=0').fetchone()[0]==b'changed'
        original.close();copy.close()
        item['isolated_write_verified']=True
        result['comparisons'].append(item)
    source.unlink()
    for item in result['comparisons']:
        if item['supported']:
            db=sqlite3.connect(str(root/(item['method']+'.sqlite')))
            assert db.execute('SELECT body FROM blocks WHERE id=0').fetchone()[0]==b'changed'
            assert db.execute('SELECT count(*) FROM blocks').fetchone()[0]==mebibytes*16
            db.close();item['source_removal_verified']=True
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mib',type=int,default=128);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='core-next-clone-') as temporary:
        result=probe(Path(temporary).resolve(),args.mib)
    Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
