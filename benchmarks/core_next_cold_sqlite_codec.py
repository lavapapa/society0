"""有限原文窗口的原生 SQLite 索引布局对照，不修改生产 codec。"""
import argparse
import json
import os
from pathlib import Path
import platform
import resource
import tempfile
import time
import zlib

import apsw


def measure(path, values, *, grouped, codec):
    path=Path(path)
    if codec=='zlib':compress=lambda value:zlib.compress(value,3);decompress=zlib.decompress
    else:
        import zstandard
        compressor=zstandard.ZstdCompressor(level=3);decoder=zstandard.ZstdDecompressor()
        compress=compressor.compress;decompress=decoder.decompress
    start=time.perf_counter();cpu=time.process_time();connection=apsw.Connection(str(path))
    connection.execute('PRAGMA journal_mode=OFF');connection.execute('PRAGMA synchronous=OFF');connection.execute('PRAGMA cache_size=-2048')
    connection.execute('CREATE TABLE records(ordinal INTEGER PRIMARY KEY,raw_bytes INTEGER NOT NULL)')
    if grouped:
        connection.execute('CREATE TABLE spans(ordinal INTEGER NOT NULL,start INTEGER NOT NULL,block INTEGER NOT NULL,offset INTEGER NOT NULL,size INTEGER NOT NULL,PRIMARY KEY(ordinal,start)) WITHOUT ROWID')
        connection.execute('CREATE TABLE blocks(id INTEGER PRIMARY KEY,payload BLOB NOT NULL)')
    else:connection.execute('CREATE TABLE chunks(ordinal INTEGER NOT NULL,chunk INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(ordinal,chunk)) WITHOUT ROWID')
    compressed_bytes=0;block=0;buffer=bytearray()
    def flush():
        nonlocal compressed_bytes,block
        if buffer:
            payload=compress(bytes(buffer));compressed_bytes+=len(payload)
            connection.execute('INSERT INTO blocks VALUES(?,?)',(block,payload));block+=1;buffer.clear()
    with connection:
        for ordinal,raw in enumerate(values):
            connection.execute('INSERT INTO records VALUES(?,?)',(ordinal,len(raw)))
            if grouped:
                offset=0
                while offset<len(raw):
                    size=min(65536-len(buffer),len(raw)-offset)
                    connection.execute('INSERT INTO spans VALUES(?,?,?,?,?)',(ordinal,offset,block,len(buffer),size))
                    buffer.extend(memoryview(raw)[offset:offset+size]);offset+=size
                    if len(buffer)==65536:flush()
            else:
                for number,offset in enumerate(range(0,len(raw),65536)):
                    payload=compress(raw[offset:offset+65536]);compressed_bytes+=len(payload)
                    connection.execute('INSERT INTO chunks VALUES(?,?,?)',(ordinal,number,payload))
        if grouped:flush()
    connection.close()
    descriptor=os.open(path,os.O_RDONLY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)
    write=time.perf_counter()-start;write_cpu=time.process_time()-cpu
    decoded=0;max_block=0
    def read(connection,ordinal,offset,size):
        nonlocal decoded,max_block
        total=connection.execute('SELECT raw_bytes FROM records WHERE ordinal=?',(ordinal,)).get
        end=min(total,offset+size)
        if end<=offset:return b''
        if grouped:
            anchor=connection.execute('SELECT start FROM spans WHERE ordinal=? AND start<=? ORDER BY start DESC LIMIT 1',(ordinal,offset)).get
            rows=connection.execute('SELECT s.start,s.offset,s.size,b.payload FROM spans s JOIN blocks b ON b.id=s.block WHERE s.ordinal=? AND s.start>=? AND s.start<? ORDER BY s.start',(ordinal,anchor,end))
        else:
            rows=((number*65536,0,min(65536,total-number*65536),payload) for number,payload in connection.execute('SELECT chunk,payload FROM chunks WHERE ordinal=? AND chunk>=? AND chunk<=? ORDER BY chunk',(ordinal,offset//65536,(end-1)//65536)))
        output=bytearray()
        for start,inside,length,payload in rows:
            raw=decompress(payload);decoded+=len(raw);max_block=max(max_block,len(raw))
            lo=max(0,offset-start);hi=min(length,end-start)
            output.extend(raw[inside+lo:inside+hi])
        return bytes(output)
    def opened():
        value=apsw.Connection(str(path),flags=apsw.SQLITE_OPEN_READONLY)
        value.execute('PRAGMA cache_size=-2048')
        return value
    connection=opened()
    try:
        for ordinal,raw in enumerate(values):
            assert read(connection,ordinal,0,len(raw))==raw
            for offset in (0,max(0,len(raw)//2-17),max(0,len(raw)-17)):
                assert read(connection,ordinal,offset,64)==raw[offset:offset+64]
        decoded=0
        positions=list(range(0,len(values),max(1,len(values)//100)))[:100]
        start=time.perf_counter();cpu=time.process_time()
        for ordinal in positions:assert read(connection,ordinal,0,len(values[ordinal]))==values[ordinal]
        reused=time.perf_counter()-start;reused_cpu=time.process_time()-cpu;read_bytes=decoded
        pages=list(connection.execute('SELECT name,sum(pgsize),sum(payload),sum(unused) FROM dbstat GROUP BY name'))
    finally:connection.close()
    start=time.perf_counter();cpu=time.process_time()
    for ordinal in positions:
        connection=opened()
        try:assert read(connection,ordinal,0,len(values[ordinal]))==values[ordinal]
        finally:connection.close()
    reopened=time.perf_counter()-start;reopened_cpu=time.process_time()-cpu
    return {'records':len(values),'raw_bytes':sum(map(len,values)),'compressed_bytes':compressed_bytes,
            'database_bytes':path.stat().st_size,'allocated_bytes':path.stat().st_blocks*512,
            'write_fsync_seconds':write,'write_cpu_seconds':write_cpu,'random_reads':len(positions),
            'random_reused_seconds':reused,'random_reused_cpu_seconds':reused_cpu,
            'random_reopened_seconds':reopened,'random_reopened_cpu_seconds':reopened_cpu,
            'random_decoded_bytes':read_bytes,'max_decoded_block_bytes':max_block,'dbstat':pages,
            'all_values_equal':True,'range_values_equal':True}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);args=parser.parse_args()
    source=apsw.Connection(str(args.source/'current.sqlite'),flags=apsw.SQLITE_OPEN_READONLY)
    total=source.execute('SELECT count(*) FROM fixture_entries').get;windows=[]
    try:
        with tempfile.TemporaryDirectory() as folder:
            for number in range(8):
                start=(total-512)*number//7;values=[];sequences=[];excluded=[]
                for seq,size in source.execute('SELECT seq,raw_bytes FROM fixture_entries WHERE seq>=? AND seq<? ORDER BY seq',(start,start+512)):
                    if size>65536:excluded.append([seq,size]);continue
                    values.append(b''.join(zlib.decompress(body) for body, in source.execute('SELECT payload FROM fixture_chunks WHERE seq=? ORDER BY chunk',(seq,))))
                    sequences.append(seq)
                routes={}
                for grouped,codec in [(False,'zlib'),(False,'zstd'),(True,'zlib'),(True,'zstd')]:
                    name=codec+('_group64k' if grouped else '_record')
                    routes[name]=measure(Path(folder)/(str(number)+name),values,grouped=grouped,codec=codec)
                windows.append({'sequences':sequences,'excluded_over_64k':excluded,'routes':routes})
    finally:source.close()
    print(json.dumps({'platform':platform.platform(),'source':str(args.source),'windows':windows,
                      'absolute_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if platform.system()=='Darwin' else 1024),
                      'limits':'preencoded original JSON; independent SQLite containers include native indexes; fsync includes file not directory; cold connection, OS cache retained; no group payload cache'}))

if __name__=='__main__':main()
