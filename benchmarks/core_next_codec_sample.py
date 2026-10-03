"""有来源记录位置的有限 codec 样本；不更改产品或重新转换大根。"""
import argparse
import json
import time
import zlib
from pathlib import Path


def measure_codec(values,compress,decompress,*,group_bytes=None):
    groups=[];positions=[];current=bytearray();current_ids=[]
    for index,raw in enumerate(values):
        if current and (group_bytes is None or len(current)+len(raw)>group_bytes):
            groups.append(bytes(current));positions.append(current_ids);current=bytearray();current_ids=[]
        current_ids.append((index,len(current),len(raw)));current.extend(raw)
    if current:groups.append(bytes(current));positions.append(current_ids)
    wall=time.perf_counter();cpu=time.process_time();encoded=[compress(raw) for raw in groups]
    compression={'wall_seconds':time.perf_counter()-wall,'cpu_seconds':time.process_time()-cpu}
    wall=time.perf_counter();cpu=time.process_time()
    for payload,rows in zip(encoded,positions):
        raw=decompress(payload)
        for index,offset,size in rows:assert raw[offset:offset+size]==values[index]
    verify={'wall_seconds':time.perf_counter()-wall,'cpu_seconds':time.process_time()-cpu}
    # 精确位置单次读取；批次路径只解对应批次，验证实际读取放大。
    sampled=list(range(0,len(values),max(1,len(values)//100)))[:100]
    mapping={index:(group,offset,size) for group,rows in enumerate(positions) for index,offset,size in rows}
    read_raw=0;wall=time.perf_counter()
    for index in sampled:
        group,offset,size=mapping[index];raw=decompress(encoded[group]);read_raw+=len(raw)
        assert raw[offset:offset+size]==values[index]
    return {'records':len(values),'groups':len(groups),'raw_bytes':sum(map(len,values)),
            'compressed_bytes':sum(map(len,encoded)),'max_group_raw_bytes':max(map(len,groups)),
            'compress':compression,'full_verify':verify,'random_reads':len(sampled),
            'random_read_seconds':time.perf_counter()-wall,'random_read_raw_bytes':read_raw,'all_values_equal':True}


def main():
    import apsw
    import zstandard as zstd
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);parser.add_argument('--windows',action='store_true');args=parser.parse_args()
    connection=apsw.Connection(str(args.run/'current.sqlite'),flags=apsw.SQLITE_OPEN_READONLY)
    count=connection.execute('SELECT count(*) FROM fixture_entries').get
    positions=sorted(set(range(min(64,count)))|{int(i*(count-1)/1023) for i in range(1024)})
    values=[];sources=[];skipped=[]
    for sequence in positions:
        size=connection.execute('SELECT raw_bytes FROM fixture_entries WHERE seq=?',(sequence,)).get
        if size>65536:skipped.append([sequence,size]);continue
        raw=b''.join(zlib.decompress(row[0]) for row in connection.execute('SELECT payload FROM fixture_chunks WHERE seq=? ORDER BY chunk',(sequence,)))
        assert len(raw)==size
        values.append(raw);sources.append(sequence)
    assert sum(map(len,values))<=16*1024*1024
    window_ranges=[((count-512)*i//7,(count-512)*i//7+512) for i in range(8)] if args.windows else []
    training=[(seq,raw) for seq,raw in zip(sources[::2],values[::2]) if not any(start<=seq<end for start,end in window_ranges)]
    train=[raw for seq,raw in training];test=values[1::2];train_start=time.perf_counter()
    dictionary=zstd.train_dictionary(32768,train)
    training_seconds=time.perf_counter()-train_start
    compressor=zstd.ZstdCompressor(level=3);decoder=zstd.ZstdDecompressor()
    trained=zstd.ZstdCompressor(level=3,dict_data=dictionary);trained_decoder=zstd.ZstdDecompressor(dict_data=dictionary)
    routes={
        'zlib3_per_record':measure_codec(test,lambda raw:zlib.compress(raw,3),zlib.decompress),
        'zstd3_per_record':measure_codec(test,compressor.compress,decoder.decompress),
        'zstd3_dictionary_per_record':measure_codec(test,trained.compress,trained_decoder.decompress),
        'zlib3_groups_64k':measure_codec(test,lambda raw:zlib.compress(raw,3),zlib.decompress,group_bytes=65536),
        'zstd3_groups_64k':measure_codec(test,compressor.compress,decoder.decompress,group_bytes=65536),
        'zlib3_groups_1mib':measure_codec(test,lambda raw:zlib.compress(raw,3),zlib.decompress,group_bytes=1048576),
        'zstd3_groups_1mib':measure_codec(test,compressor.compress,decoder.decompress,group_bytes=1048576),
    }
    paths=[];bodies=[]
    for raw in test:
        entry=json.loads(raw);paths.append(json.dumps(entry.pop('path'),ensure_ascii=False,separators=(',',':')).encode())
        bodies.append(json.dumps(entry,ensure_ascii=False,separators=(',',':')).encode())
    path_split={'path_raw_bytes':sum(map(len,paths)),'body_raw_bytes':sum(map(len,bodies)),
                'path_grouped_compressed_bytes':len(zlib.compress(b''.join(paths),3)),
                'body_independent_compressed_bytes':sum(len(zlib.compress(raw,3)) for raw in bodies)}
    large=connection.execute("SELECT seq FROM fixture_selection WHERE kind='large'").get
    chunk_count=connection.execute('SELECT count(*) FROM fixture_chunks WHERE seq=?',(large,)).get
    large_blocks=[];large_positions=sorted({0,chunk_count//2,chunk_count-1})
    for index in large_positions:
        payload=connection.execute('SELECT payload FROM fixture_chunks WHERE seq=? AND chunk=?',(large,index)).get
        large_blocks.append(zlib.decompress(payload))
    large_routes={
        'zlib3':measure_codec(large_blocks,lambda raw:zlib.compress(raw,3),zlib.decompress),
        'zstd3':measure_codec(large_blocks,compressor.compress,decoder.decompress),
    }
    windows=[]
    if args.windows:
        for window_index in range(8):
            start=(count-512)*window_index//7
            window=[];window_sources=[];excluded=[]
            for sequence,size in connection.execute('SELECT seq,raw_bytes FROM fixture_entries WHERE seq>=? AND seq<? ORDER BY seq',(start,start+512)):
                if size>65536:excluded.append(sequence);continue
                raw=b''.join(zlib.decompress(row[0]) for row in connection.execute('SELECT payload FROM fixture_chunks WHERE seq=? ORDER BY chunk',(sequence,)))
                window.append(raw);window_sources.append(sequence)
            assert sum(map(len,window))<=16*1024*1024
            windows.append({'start':start,'sequences':window_sources,'excluded_over_64k':excluded,'routes':{
                'zlib3_per_record':measure_codec(window,lambda raw:zlib.compress(raw,3),zlib.decompress),
                'zstd3_dictionary_per_record':measure_codec(window,trained.compress,trained_decoder.decompress),
                'zlib3_groups_64k':measure_codec(window,lambda raw:zlib.compress(raw,3),zlib.decompress,group_bytes=65536),
                'zstd3_groups_64k':measure_codec(window,compressor.compress,decoder.decompress,group_bytes=65536),
                'zlib3_groups_1mib':measure_codec(window,lambda raw:zlib.compress(raw,3),zlib.decompress,group_bytes=1048576),
                'zstd3_groups_1mib':measure_codec(window,compressor.compress,decoder.decompress,group_bytes=1048576),
            }})
    connection.close()
    print(json.dumps({'source_run':str(args.run),'source_records':count,'sample_sequences':sources,'excluded_over_64k':skipped,
        'sample_raw_bytes':sum(map(len,values)),'train_sequences':[seq for seq,raw in training],'test_sequences':sources[1::2],
        'zstd_version':zstd.__version__,'dictionary_bytes':len(dictionary.as_bytes()),'training_seconds':training_seconds,
        'routes':routes,'path_split':path_split,'continuous_windows':windows,'large_entry':large,'large_chunk_positions':large_positions,'large_routes':large_routes,
        'scope':'bounded representative samples; codec sizes exclude SQL/index metadata; dictionary trained on disjoint positions'}))

if __name__=='__main__':main()
