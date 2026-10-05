"""每种输入由独立进程测量；不调用模型。"""
import base64
import json
from pathlib import Path
import platform
import random
import resource
import subprocess
import sys
import tempfile
import time
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA


def probe(kind):
    text = 'a' * (11 * 1024 * 1024) if kind == 'repeat' else base64.b64encode(random.Random(47).randbytes(8 * 1024 * 1024)).decode('ascii')
    baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = StageStore.create(root / 'run', THREAD_SCHEMA)
        threads = ThreadStore(store)
        thread = threads.open('a', 0, 'operating')
        start, cpu = time.perf_counter(), time.process_time()
        threads.append_message(thread, {'role':'user','content':text})
        write_wall, write_cpu = time.perf_counter()-start, time.process_time()-cpu
        capture = store._session.memory_used
        start, cpu = time.perf_counter(), time.process_time()
        marker = store.complete(1)
        complete_wall, complete_cpu = time.perf_counter()-start, time.process_time()-cpu
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        store.close()
        current_bytes = (root/'run/current.sqlite').stat().st_size
        initial_root_bytes = (root/'run/root.sqlite').stat().st_size
        changeset_bytes = (root/'run'/marker['changeset']).stat().st_size
        start = time.perf_counter()
        with StageStore.restore(root/'run',root/'restored') as restored:
            assert ThreadStore(restored).read_messages(thread) == [{'role':'user','content':text}]
        restored_root_bytes = (root/'restored/root.sqlite').stat().st_size
        return dict(kind=kind,input_text_bytes=len(text.encode()),current_bytes=current_bytes,initial_root_bytes=initial_root_bytes,
                    changeset_bytes=changeset_bytes,restored_root_bytes=restored_root_bytes,write_wall_s=write_wall,write_cpu_s=write_cpu,
                    complete_wall_s=complete_wall,complete_cpu_s=complete_cpu,restore_and_read_wall_s=time.perf_counter()-start,
                    session_capture_bytes=capture,baseline_peak_rss_bytes=baseline,write_peak_rss_bytes=peak,
                    high_water_increment_bytes=peak-baseline,all_values_equal=True)


if __name__ == '__main__':
    if len(sys.argv)>1:
        print(json.dumps(probe(sys.argv[1])))
    else:
        results=[json.loads(subprocess.check_output([sys.executable,__file__,kind])) for kind in ('repeat','heterogeneous')]
        print(json.dumps(dict(platform=platform.platform(),python=sys.version,rss_unit='bytes on macOS',cases=results),indent=2))
