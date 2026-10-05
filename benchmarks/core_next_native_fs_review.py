"""非作者检查原生 OverlayFs 跨激活目录操作与 SQL 原子应用。"""
import asyncio
import json
import tempfile
from pathlib import Path
from bashkit import Bash,FileSystem
from society0_fs_probe import Overlay,callback_filesystem
from benchmarks.core_next_native_fs_probe import CurrentFiles

async def main():
    with tempfile.TemporaryDirectory() as temp:
        lower=CurrentFiles(str(Path(temp)/'current.sqlite'))
        async def run(command):
            overlay=Overlay(callback_filesystem(lower.callback));bash=Bash()
            bash.mount('/workspace',FileSystem.from_capsule(overlay.capsule()))
            result=await bash.execute(command)
            assert result.exit_code==0,(command,result.stderr)
            lower.apply(overlay.changes())
            return result.stdout
        await run('mkdir -p /workspace/a/nested; printf original > /workspace/a/nested/item; printf replaced > /workspace/dest')
        await run('mv /workspace/a/nested/item /workspace/dest')
        assert await run('cat /workspace/dest')=='original'
        await run('mv /workspace/a /workspace/b')
        assert await run('test -d /workspace/b/nested; test ! -e /workspace/a; echo ok')=='ok\n'
        await run('printf old > /workspace/b/nested/dead')
        await run('rm -r /workspace/b; mkdir /workspace/b; printf fresh > /workspace/b/new')
        assert await run('test ! -e /workspace/b/nested/dead; cat /workspace/b/new')=='fresh'
        old=lower.rows()
        try:lower.apply({'removed':['/dest'],'entries':[{'path':'/broken','kind':'file','content':b'z'}]})
        except KeyError:pass
        else:raise AssertionError('expected malformed delta error')
        assert lower.rows()==old
        await lower.close();lower.db.close()
        print(json.dumps({'directory_rename':True,'overwrite_rename':True,'delete_recreate':True,'failed_apply_rollback':True}))

if __name__=='__main__':asyncio.run(main())
