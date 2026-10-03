"""文件系统对不可变正文 hardlink 的独立试验，不处理可写 SQLite。"""
import argparse
import json
import os
from pathlib import Path
import platform
import random
import tempfile
import time


def probe(folder,*,blocks=64):
    folder=Path(folder);source=folder/'source';target=folder/'target'
    source.mkdir();target.mkdir();original=source/'sealed';linked=target/'sealed'
    rng=random.Random(407)
    with original.open('wb') as stream:
        for _ in range(blocks):stream.write(rng.randbytes(1048576))
        stream.flush();os.fsync(stream.fileno())
    started=time.perf_counter();os.link(original,linked)
    fd=os.open(target,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
    elapsed=time.perf_counter()-started
    before=original.stat();copy=linked.stat()
    same=(before.st_dev,before.st_ino)==(copy.st_dev,copy.st_ino)
    original.unlink();source.rmdir()
    rng=random.Random(407)
    with linked.open('rb') as stream:
        for _ in range(blocks):assert stream.read(1048576)==rng.randbytes(1048576)
        assert stream.read(1)==b''
    return {'platform':platform.platform(),'logical_bytes_per_reference':copy.st_size,
            'same_inode':same,'link_count_before_source_delete':before.st_nlink,
            'summed_st_blocks_bytes':512*(before.st_blocks+copy.st_blocks),
            'unique_allocated_bytes':512*before.st_blocks if same else 512*(before.st_blocks+copy.st_blocks),
            'link_and_directory_fsync_seconds':elapsed,'target_values_equal_after_source_delete':True,
            'scope':'same-filesystem immutable body only; modification is outside this contract; no SQLite mutable-file sharing'}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--parent',type=Path,default=None);args=parser.parse_args()
    with tempfile.TemporaryDirectory(dir=args.parent) as folder:print(json.dumps(probe(folder)))
