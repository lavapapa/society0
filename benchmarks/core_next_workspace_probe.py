"""私有工作区快照与现成挂载的成本探针；不提供版本化文件系统。"""
import json
import statistics
import random
import tempfile
import time
from pathlib import Path
from bashkit import Bash,FileSystem


def snapshots(size,steps=10):
    content=random.Random(913).randbytes(size)
    bash=Bash()
    bash.fs().mkdir('/workspace',recursive=True)
    bash.fs().write_file('/workspace/note',content)
    snapshots=[];times=[];shell_sizes=[]
    for _ in range(steps):
        bash.execute_sync('cd /workspace; retained=original; true')
        start=time.perf_counter();data=bash.snapshot();times.append(time.perf_counter()-start)
        snapshots.append(len(data));shell_sizes.append(len(bash.snapshot(exclude_filesystem=True)))
        bash=Bash.from_snapshot(data)
        assert bash.fs().read_file('/workspace/note')==content
    return {'workspace_bytes':size,'content':'Random(913).randbytes','activations':steps,'snapshot_bytes':snapshots,
            'total_snapshot_bytes':sum(snapshots),'median_snapshot_seconds':statistics.median(times),
            'shell_only_bytes':shell_sizes}


def mount_probe():
    with tempfile.TemporaryDirectory() as folder:
        path=Path(folder);(path/'original.txt').write_text('完整原文🙂',encoding='utf-8')
        bash=Bash();bash.mount('/world/docs',FileSystem.real(str(path)),read_only=True)
        result=bash.execute_sync('ls /world/docs; cat /world/docs/original.txt')
        assert result.stdout=='original.txt\n完整原文🙂'
        failed=bash.execute_sync('echo overwrite > /world/docs/original.txt')
        assert failed.exit_code!=0 and (path/'original.txt').read_text()=='完整原文🙂'
        bash.execute_sync('cd /world/docs; remembered=kept')
        state=bash.snapshot(exclude_filesystem=True)
        resumed=Bash.from_snapshot(state)
        resumed.mount('/world/docs',FileSystem.real(str(path)),read_only=True)
        assert resumed.execute_sync('printf "%s|" "$remembered"; pwd; cat original.txt').stdout=='kept|/world/docs\n完整原文🙂'
        return {'read_only_real_mount':True,'shell_only_restore_remount':True,'snapshot_bytes':len(state)}


def lazy_probe(size=8*1024*1024):
    calls=[]
    def load():calls.append(size);return 'x'*size
    bash=Bash(files={'/world/document':load})
    listing=bash.execute_sync('ls /world')
    assert 'document' in listing.stdout
    before=list(calls)
    bash.execute_sync('head -c 16 /world/document')
    first=list(calls)
    bash.execute_sync('head -c 16 /world/document')
    return {'document_bytes':size,'loader_calls_after_listing':before,
            'loader_calls_after_first_head':first,'loader_calls_after_second_head':calls,
            'snapshot_bytes':len(bash.snapshot()),'shell_only_snapshot_bytes':len(bash.snapshot(exclude_filesystem=True))}


if __name__=='__main__':
    print(json.dumps({'format':1,'library':'bashkit 0.18.2','unchanged':[snapshots(1024*1024),snapshots(8*1024*1024)],
                      'mount':mount_probe(),'lazy':lazy_probe()},indent=2))
