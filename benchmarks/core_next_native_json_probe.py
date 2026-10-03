"""临时依赖实验：成熟原生 JSON 流编码与全量编码的值、时间和内存。"""
import argparse
import io
import json
import math
import platform
import resource
import tempfile
import time

# 固定旧 Python 编码基线，独立于当前产品 push API。
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('json_baseline',Path(__file__).parents[1]/'research/core-next/json-codec-baseline-f4d03a6.py')
_baseline=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_baseline)
raw_chunks=_baseline.raw_chunks


def native_options(module):
    return dict(ensure_ascii=False,allow_nan=False,number_mode=module.NM_NONE,
                bytes_mode=module.BM_NONE,iterable_mode=module.IM_ONLY_LISTS,mapping_mode=module.MM_ONLY_DICTS)


def validate():
    import rapidjson
    options=native_options(rapidjson)
    value={'big':2**300,'negative':-(2**300),'float':1.2345678901234567,'zero':-0.0,
           'list':[False,None,3,1],'unicode':'汉🙂\\"\x00'*10000}
    stream=io.BytesIO();rapidjson.dump(value,stream,chunk_size=65536,**options)
    decoded=json.loads(stream.getvalue())
    assert decoded==value and math.copysign(1,decoded['zero'])==-1
    cyclic=[];cyclic.append(cyclic)
    for bad in [{1:'integer key'},float('nan'),float('inf'),cyclic,(1,2),b'bytes']:
        try:rapidjson.dump(bad,io.BytesIO(),**options)
        except (ValueError,TypeError,RecursionError):pass
        else:raise AssertionError('invalid value accepted')
    return True


def rss():
    try:
        with open('/proc/self/status') as stream:
            return int(next(line for line in stream if line.startswith('VmRSS:')).split()[1])*1024
    except FileNotFoundError:return None


def measure(value,mode):
    import rapidjson
    baseline=rss();before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with tempfile.TemporaryFile() as output:
        class Sink:
            total=0;calls=0;largest=0
            def write(self,data):
                if isinstance(data,str):data=data.encode('utf8')
                self.total+=len(data);self.calls+=1;self.largest=max(self.largest,len(data))
                return output.write(data)
        sink=Sink();start=time.perf_counter();cpu=time.process_time()
        if mode=='python_stream':
            for chunk in raw_chunks(value):sink.write(chunk)
        elif mode=='python_whole':sink.write(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False))
        elif mode=='rapidjson_whole':sink.write(rapidjson.dumps(value,**native_options(rapidjson)))
        elif mode=='rapidjson_stream':rapidjson.dump(value,sink,chunk_size=65536,**native_options(rapidjson))
        else:raise ValueError(mode)
        wall=time.perf_counter()-start;cpu=time.process_time()-cpu
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        retained=rss();output.seek(0);actual=json.load(output)
        assert actual==value
    factor=1 if platform.system()=='Darwin' else 1024
    return {'mode':mode,'wall_seconds':wall,'cpu_seconds':cpu,'raw_bytes':sink.total,
            'write_calls':sink.calls,'max_write_bytes':sink.largest,'values_equal':True,
            'baseline_rss_bytes':baseline,'after_encode_rss_bytes':retained,
            'preencode_highwater_bytes':before*factor,'encode_highwater_bytes':peak*factor,
            'limit':'highwater includes initial input load; file write included, fsync excluded; verification outside timing; input value remains resident'}


def utf8_cache_probe():
    import rapidjson,sys,gc,tracemalloc
    class Sink:
        def write(self,raw):return len(raw)
    tracemalloc.start();text='汉🙂'*1000000
    before=sys.getsizeof(text);allocated_before=tracemalloc.get_traced_memory()[0]
    rapidjson.dump(text,Sink(),ensure_ascii=False,chunk_size=65536)
    after=sys.getsizeof(text);allocated_after=tracemalloc.get_traced_memory()[0]
    rapidjson.dump(text,Sink(),ensure_ascii=False,chunk_size=65536)
    repeat=sys.getsizeof(text)
    del text;gc.collect();released=tracemalloc.get_traced_memory()[0]
    assert after-before==7_000_001 and repeat==after and released<100000
    tracemalloc.stop()
    return {'string_size_before':before,'string_size_after':after,'string_size_after_second_dump':repeat,
            'size_increase':after-before,'expected_utf8_bytes_plus_nul':7_000_001,
            'traced_before':allocated_before,'traced_after':allocated_after,
            'traced_after_input_release':released,'rapidjson':rapidjson.__version__}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',required=True)
    parser.add_argument('--source');parser.add_argument('--sequence',type=int,default=4);parser.add_argument('--kind',choices=['complex','string'],default='complex')
    args=parser.parse_args()
    if args.mode=='utf8_cache':
        print(json.dumps(utf8_cache_probe()));return
    assert validate()
    if args.source:
        import apsw,zlib
        connection=apsw.Connection(args.source+'/current.sqlite',flags=apsw.SQLITE_OPEN_READONLY)
        raw=bytearray()
        for body, in connection.execute('SELECT payload FROM fixture_chunks WHERE seq=? ORDER BY chunk',(args.sequence,)):
            raw.extend(zlib.decompress(body))
        value=json.loads(raw);del raw;connection.close()
    elif args.kind=='string':value={'text':'汉🙂\\"\x00'*1000000}
    else:value={'rows':[{'id':i,'name':'角色'+str(i),'values':[i%17,1.2345678901234567,False,None]} for i in range(100000)]}
    result=measure(value,args.mode)
    import rapidjson
    result.update(platform=platform.platform(),python=platform.python_version(),rapidjson=rapidjson.__version__,source=args.source,sequence=args.sequence,kind=args.kind,contract_checked=True)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
