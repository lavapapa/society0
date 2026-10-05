import asyncio,json,platform,pathlib,society0,society0_filesystem
from society0.kernel.information_fs import search_reader
async def main():
 body=('关键词 full original\n'*5000).encode(); hits=[]
 async def read(offset,size):return body[offset:offset+size]
 async def sink(line,offset,data):hits.append((line,offset,data))
 result=await search_reader(read,sink,'关(?:键)词')
 line='关键词 full original\n'.encode()
 assert hits==[(i+1,i*len(line),line) for i in range(5000)]
 assert result['matches']==5000 and result['sink_batches']<20
 output={'platform':platform.platform(),'society0':society0.__file__,'native':society0_filesystem.__file__,'reader':result,'hits_original_bytes':True,'outside_source_cwd':str(pathlib.Path.cwd())}
 print(json.dumps(output,ensure_ascii=False,indent=2))
asyncio.run(main())
