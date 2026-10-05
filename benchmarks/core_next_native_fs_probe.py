"""用 Bashkit 现成 OverlayFs 验证动态 SQL 下层；此文件不是产品文件系统。"""
import asyncio
import json
import resource
import sqlite3
import tempfile
import time
from pathlib import Path
from bashkit import Bash, FileSystem
from society0_fs_probe import Overlay, callback_filesystem

class CurrentFiles:
    def __init__(self, path):
        self.db=sqlite3.connect(path)
        self.db.execute('CREATE TABLE entries(path TEXT PRIMARY KEY,parent TEXT,kind TEXT,body BLOB,mode INTEGER)')
        self.db.execute('CREATE INDEX by_parent ON entries(parent,path)')
        self.db.execute("INSERT INTO entries VALUES('/','', 'directory',NULL,493)")
        self.active=True;self.calls=[];self.body_bytes=0;self.block=None;self.entered=asyncio.Event();self.tasks=set()
    def file(self,path,body):
        self.db.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?,?,420)',(path,str(Path(path).parent),'file',body));self.db.commit()
    async def callback(self,operation,path):
        task=asyncio.current_task();self.tasks.add(task)
        try:return await self._callback(operation,path)
        finally:self.tasks.discard(task)
    async def close(self):
        self.active=False
        for task in tuple(self.tasks):task.cancel()
        await asyncio.gather(*tuple(self.tasks),return_exceptions=True)
    async def _callback(self,operation,path):
        if not self.active:raise RuntimeError('scope closed')
        self.calls.append((operation,path))
        if operation=='read' and self.block:
            self.entered.set();await self.block.wait()
        if not self.active:raise RuntimeError('scope closed')
        if operation=='list':
            return [(Path(p).name,k=='directory',n or 0,m) for p,k,n,m in self.db.execute('SELECT path,kind,length(body),mode FROM entries WHERE parent=? ORDER BY path',(path,))]
        row=self.db.execute('SELECT kind,length(body),mode FROM entries WHERE path=?',(path,)).fetchone()
        if operation=='exists':return row is not None
        if row is None:raise FileNotFoundError(path)
        if operation=='stat':return row[0]=='directory',row[1] or 0,row[2]
        if operation=='read':
            body=self.db.execute('SELECT body FROM entries WHERE path=?',(path,)).fetchone()[0]
            self.body_bytes+=len(body);return body
        raise ValueError(operation)
    def apply(self,delta):
        with self.db:
            for path in delta['removed']:
                self.db.execute('DELETE FROM entries WHERE path=? OR substr(path,1,?)=?',(path,len(path)+1,path+'/'))
            for entry in delta['entries']:
                path=entry['path']
                self.db.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?)',(path,'' if path=='/' else str(Path(path).parent),entry['kind'],entry.get('content'),entry['mode']))
    def rows(self):return self.db.execute('SELECT path,kind,body,mode FROM entries ORDER BY path').fetchall()

def changed_bytes(delta):return sum(len(e.get('content',b'')) for e in delta['entries'])

async def main():
    with tempfile.TemporaryDirectory() as folder:
        lower=CurrentFiles(str(Path(folder)/'current.sqlite'))
        body=b'x'*(8*1024*1024);lower.file('/cold',body)
        phases=[];shell_state=None
        for command in ('head -c 16 /workspace/cold; answer=kept; cd /workspace', 'printf small > /workspace/new; mv /workspace/new /workspace/renamed', 'rm /workspace/renamed; printf "%s" "$answer"'):
            overlay=Overlay(callback_filesystem(lower.callback));fs=FileSystem.from_capsule(overlay.capsule())
            bash=Bash.from_snapshot(shell_state) if shell_state else Bash()
            bash.mount('/workspace',fs)
            before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;start=time.perf_counter()
            result=await bash.execute(command)
            assert result.exit_code==0,result.stderr
            delta=overlay.changes();lower.apply(delta)
            shell_state=bash.snapshot(exclude_filesystem=True)
            phases.append({'changed_body_bytes':changed_bytes(delta),'removed':delta['removed'],'shell_state_bytes':len(shell_state),'elapsed_seconds':time.perf_counter()-start,'rss_peak_increment_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss-before})
        assert phases[0]['changed_body_bytes']==0 and phases[1]['changed_body_bytes']==5 and phases[2]['changed_body_bytes']==0
        assert result.stdout=='kept'
        assert lower.db.execute("SELECT body FROM entries WHERE path='/cold'").fetchone()[0]==body
        assert lower.db.execute("SELECT count(*) FROM entries WHERE path IN ('/new','/renamed')").fetchone()[0]==0
        checkpoint=sqlite3.connect(str(Path(folder)/'complete.sqlite'));lower.db.backup(checkpoint)
        overlay=Overlay(callback_filesystem(lower.callback));appender=Bash();appender.mount('/workspace',FileSystem.from_capsule(overlay.capsule()))
        await appender.execute('printf z >> /workspace/cold')
        append_delta=overlay.changes();assert changed_bytes(append_delta)==len(body)+1
        lower.apply(append_delta)
        checkpoint.backup(lower.db);checkpoint.close()
        assert lower.db.execute("SELECT body FROM entries WHERE path='/cold'").fetchone()[0]==body
        # 动态共享目录不预列全对象；增删后新命令直接读当前 SQL 目录。
        world=Bash();world.mount('/world',FileSystem.from_capsule(callback_filesystem(lower.callback)),read_only=True)
        assert 'cold' in (await world.execute('ls /world')).stdout.splitlines()
        lower.file('/later',b'new fact')
        assert (await world.execute('cat /world/later')).stdout=='new fact'
        # 关闭作用域时，正在等待的读取无法继续返回正文。
        lower.block=asyncio.Event();job=asyncio.ensure_future(world.execute('cat /world/cold'))
        await lower.entered.wait();lower.active=False;lower.block.set()
        result=await job
        assert result.exit_code!=0 and result.stdout==''
        lower.active=True;lower.block=asyncio.Event();lower.entered=asyncio.Event()
        pending=asyncio.ensure_future(world.execute('cat /world/cold'));await lower.entered.wait();pending.cancel()
        await lower.close();await asyncio.gather(pending,return_exceptions=True)
        assert not lower.tasks
        print(json.dumps({'library':'bashkit 0.18.2','bridge':'native OverlayFs + stable capsule ABI','phases':phases,'lower_body_bytes_read':lower.body_bytes,'large_append_changed_body_bytes':changed_bytes(append_delta),'old_complete_restored':True,'callback_close_drained':True,'closed_scope_rejected':True,'sql_current_index_no_overlay_chain':True,'first_head_file_bytes':len(body),'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},indent=2))

if __name__=='__main__':asyncio.run(main())
