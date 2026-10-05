"""隔离格式选择试验；全部正文合成，无模型请求，不修改产品。"""
import argparse, base64, importlib.metadata, importlib.util, json, os, pathlib, platform, random, resource, sqlite3, subprocess, sys, tempfile, time
import pyzstd, zstandard

BLOCK=65536

def peak():
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform=='darwin' else value*1024

class Counter:
    def __init__(self,file):self.file=file;self.bytes=0;self.calls=0
    def read(self,n=-1):
        data=self.file.read(n);self.bytes+=len(data);self.calls+=1;return data
    def __getattr__(self,name):return getattr(self.file,name)

def fixture(folder):
    rng=random.Random(20261004); locations=[]
    with (folder/'raw').open('wb') as out:
        for i in range(12000):
            value={'id':i,'owner':i%37,'note':'原文🙂报价与路径保持 /a/b '+str(i%11),
                   'quantity':i%173,'amount':i/7,'body':base64.b64encode(rng.randbytes(900)).decode(),
                   'history':[{'kind':'production','status':'completed','ordinal':i%97}]*3}
            if i==6000:value['body']='汉🙂'+base64.b64encode(rng.randbytes(2*1024*1024)).decode()
            raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
            locations.append((i,out.tell(),len(raw)));out.write(raw)
    (folder/'locations.json').write_text(json.dumps(locations))
    return (folder/'raw').stat().st_size

def cold(folder,mode):
    db=sqlite3.connect(folder/(mode+'.sqlite'));db.execute('CREATE TABLE records(ordinal INTEGER PRIMARY KEY,start INTEGER,size INTEGER)')
    db.executemany('INSERT INTO records VALUES(?,?,?)',json.loads((folder/'locations.json').read_text()));db.commit()
    inp=folder/'raw';out=folder/(mode+'.zst');c=zstandard.ZstdCompressor(level=3)
    started=time.perf_counter();cpu=time.process_time()
    if mode=='sqlite64':
        db.execute('CREATE TABLE blocks(id INTEGER PRIMARY KEY,body BLOB)')
        with inp.open('rb') as source,db:
            for i,raw in enumerate(iter(lambda:source.read(BLOCK),b'')):db.execute('INSERT INTO blocks VALUES(?,?)',(i,c.compress(raw)))
    elif mode=='stream4':
        with inp.open('rb') as source,out.open('wb') as target:
            zstandard.ZstdCompressor(level=3,threads=4).copy_stream(source,target)
    else:
        frame,workers=({'seek64_0':(BLOCK,0),'seek64_4':(BLOCK,4),'seek1m_0':(1048576,0),'seek1m_4':(1048576,4)})[mode]
        with inp.open('rb') as source,pyzstd.SeekableZstdFile(out,'wb',max_frame_content_size=frame,
              level_or_option={pyzstd.CParameter.compressionLevel:3,pyzstd.CParameter.nbWorkers:workers}) as target:
            for raw in iter(lambda:source.read(BLOCK),b''):target.write(raw)
    db.commit()
    for path in (folder/(mode+'.sqlite'),out):
        if path.exists():
            with path.open('rb') as f:os.fsync(f.fileno())
    result={'mode':mode,'write_wall_s':time.perf_counter()-started,'write_cpu_s':time.process_time()-cpu,'write_peak_bytes':peak(),
            'disk_bytes':sum(p.stat().st_size for p in (folder/(mode+'.sqlite'),out) if p.exists()),'range_queries':0}
    # 顺序验证逐个64KiB块；源文件在页缓存内，未声称物理冷盘。
    reader=counter=None
    if mode.startswith('seek'):
        counter=Counter(out.open('rb'));opened=time.perf_counter();reader=pyzstd.SeekableZstdFile(counter,'rb')
        result['reader_open_s']=time.perf_counter()-opened;result['open_read_bytes']=counter.bytes
    elif mode=='stream4':reader=zstandard.ZstdDecompressor().stream_reader(out.open('rb'))
    with inp.open('rb') as source:
        i=0
        for raw in iter(lambda:source.read(BLOCK),b''):
            actual=zstandard.ZstdDecompressor().decompress(db.execute('SELECT body FROM blocks WHERE id=?',(i,)).fetchone()[0]) if mode=='sqlite64' else reader.read(len(raw))
            assert actual==raw;i+=1
    result['full_bytes_equal']=True
    if mode!='stream4':
        rng=random.Random(947);wanted=[]
        for _ in range(500):
            ordinal=rng.randrange(12000);start,size=db.execute('SELECT start,size FROM records WHERE ordinal=?',(ordinal,)).fetchone()
            offset=rng.randrange(size);wanted.append((start+offset,min(64,size-offset)))
        # 另测巨值尾部及跨帧范围。
        start,size=db.execute('SELECT start,size FROM records WHERE ordinal=6000').fetchone();wanted += [(start+size-64,64),((start//BLOCK+1)*BLOCK-7,128)]
        before=counter.bytes if counter else 0;started=time.perf_counter();cpu=time.process_time()
        with inp.open('rb') as source:
            for start,size in wanted:
                if mode=='sqlite64':
                    first,last=start//BLOCK,(start+size-1)//BLOCK
                    raw=b''.join(zstandard.ZstdDecompressor().decompress(row[0]) for row in db.execute('SELECT body FROM blocks WHERE id BETWEEN ? AND ? ORDER BY id',(first,last)))
                    actual=raw[start-first*BLOCK:start-first*BLOCK+size]
                else:reader.seek(start);actual=reader.read(size)
                source.seek(start);assert actual==source.read(size)
        result.update(range_wall_s=time.perf_counter()-started,range_cpu_s=time.process_time()-cpu,range_queries=len(wanted),
                      range_bytes_equal=True,compressed_read_bytes=counter.bytes-before if counter else None)
    if reader:reader.close()
    if counter:counter.file.close()
    db.close();return result

def thread_body(folder,mode,product):
    # 四条10MiB正文，输入只保一份；每条以文件充当最终sink，包含实际写盘。
    spec=importlib.util.spec_from_file_location('fixture_codec',product);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    body=base64.b64encode(random.Random(44).randbytes(7_864_320)).decode();value={'role':'assistant','content':body}
    cpu=time.process_time();started=time.perf_counter();pool=None
    if mode=='zlib_pool':pool=module.ChunkEncoder(4,8*BLOCK)
    c=zstandard.ZstdCompressor(level=3);paths=[]
    import backports.zstd
    native=backports.zstd.ZstdCompressor(level=3)
    for n in range(4):
        path=folder/f'{mode}_{n}';paths.append(path)
        with path.open('wb') as out:
            if mode=='zlib_pool':pool.write(value,lambda size,data:out.write(len(data).to_bytes(4,'little')+data))
            elif mode=='native_serial64':module.write_json(value,lambda raw:out.write(len(data:=native.compress(raw,native.FLUSH_FRAME)).to_bytes(4,'little')+data))
            elif mode=='zstd_serial64':module.write_json(value,lambda raw:out.write(len(data:=c.compress(raw)).to_bytes(4,'little')+data))
            else:
                with pyzstd.SeekableZstdFile(out,'wb',max_frame_content_size=1048576,level_or_option={pyzstd.CParameter.compressionLevel:3,pyzstd.CParameter.nbWorkers:4}) as target:module.write_json(value,target.write)
            out.flush();os.fsync(out.fileno())
    if pool:pool.close()
    result={'mode':mode,'write_wall_s':time.perf_counter()-started,'write_cpu_s':time.process_time()-cpu,'write_peak_bytes':peak(),'disk_bytes':sum(p.stat().st_size for p in paths),'events':4,'each_body_bytes':len(body)}
    import zlib
    for path in paths:
        if mode=='seek1m4_event':
            with pyzstd.SeekableZstdFile(path,'rb') as f:decoded=json.load(f)
        else:
            values=[]
            with path.open('rb') as f:
                while (head:=f.read(4)):
                    data=f.read(int.from_bytes(head,'little'));values.append(zlib.decompress(data) if mode=='zlib_pool' else zstandard.ZstdDecompressor().decompress(data))
            decoded=json.loads(b''.join(values))
        assert decoded==value
    result['all_values_equal']=True;return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--worker');parser.add_argument('--folder');parser.add_argument('--product');parser.add_argument('--output');args=parser.parse_args()
    if args.worker:
        folder=pathlib.Path(args.folder)
        result=thread_body(folder,args.worker,args.product) if args.worker in ('zlib_pool','zstd_serial64','native_serial64','seek1m4_event') else cold(folder,args.worker)
        print(json.dumps(result));return
    results={'environment':{'python':sys.version,'platform':platform.platform(),'cpu_count':os.cpu_count(),'versions':{p:importlib.metadata.version(p) for p in ('pyzstd','backports-zstd','zstandard','apsw','python-rapidjson')}},'results':[]}
    with tempfile.TemporaryDirectory(prefix='society0-selection-') as directory:
        folder=pathlib.Path(directory);results['raw_bytes']=fixture(folder)
        for mode in ('sqlite64','seek64_0','seek64_4','seek1m_0','seek1m_4','stream4','zlib_pool','zstd_serial64','native_serial64','seek1m4_event'):
            cmd=[sys.executable,__file__,'--worker',mode,'--folder',directory,'--product',args.product]
            results['results'].append(json.loads(subprocess.check_output(cmd,text=True)))
            pathlib.Path(args.output).write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))
if __name__=='__main__':main()
