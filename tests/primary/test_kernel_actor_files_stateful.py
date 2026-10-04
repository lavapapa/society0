"""真实工作区与完整点恢复，对照独立文件字典的状态机。"""
import asyncio
import base64
from copy import deepcopy
from pathlib import Path
import shlex
import tempfile
from types import SimpleNamespace

from hypothesis import settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, precondition

from society0.kernel.actors import ACTOR_SCHEMA
from society0.kernel.actor_files import ActorFiles
from society0.kernel.activation import ActivationContext
from society0.kernel.interaction import Actions, Information, InteractionScope, Moment
from society0.kernel.shell import ShellSession
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from society0.kernel.workspace import WORKSPACE_SCHEMA, WorkspaceStore


class FilesMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.loop=asyncio.Runner();self.branch=0;self.actor='a'
        self.oracle={'a':{},'b':{}};self.frozen=deepcopy(self.oracle)
        self.store=StageStore.create(self.root/'run0',(*ACTOR_SCHEMA,*WORKSPACE_SCHEMA,*THREAD_SCHEMA),
            initialize=lambda w:w.executemany('INSERT INTO actors(id,driver,active) VALUES(?,?,1)',[('a','rule'),('b','rule')]))
        self.open()

    def call(self,coro):return self.loop.run(coro)

    def open(self):self.call(self._open())

    async def _open(self):
        self.scope=InteractionScope(self.actor,Moment(self.store.complete_step+1,'files'))
        info=Information(lambda *a:True)
        self.shell=ShellSession(self.scope,info,Actions(lambda *a:True),result_dir=self.root/'outputs',workspace=WorkspaceStore(self.store))
        current=SimpleNamespace(actor=SimpleNamespace(id=self.actor),scope=self.scope,moment=self.scope.moment,
            step=self.store.complete_step+1,information=info.bound(self.scope),cursors={},prepare_artifact=self.store.prepare_artifact)
        current.cursors['activation']=ActivationContext(current)
        threads=ThreadStore(self.store);current.cursors['thread_id']=threads.open(self.actor,self.scope.moment,'decision')
        self.files=ActorFiles(current,threads,shell=self.shell);self.shell.bind_files(self.files)

    def close(self,save=True):
        if save:self.shell.save_workspace()
        self.call(self.shell.aclose());self.call(self.files.close());self.scope.close()

    @rule(name=st.sampled_from(('n0','n1','n2')),body=st.text(alphabet='中🙂abc\n',max_size=30))
    def write(self,name,body):
        result=self.call(self.shell.execute('printf %s '+shlex.quote(body)+' > /workspace/'+name))
        assert result.exit_code==0
        self.oracle[self.actor][name]=body.encode()

    @rule(name=st.sampled_from(('n0','n1','n2')))
    def remove(self,name):
        assert self.call(self.shell.execute('rm -f /workspace/'+name)).exit_code==0
        self.oracle[self.actor].pop(name,None)

    @precondition(lambda self:bool(self.oracle[self.actor]))
    @rule()
    def changed_version_expires_previous_read(self):
        name=next(iter(self.oracle[self.actor]))
        previous=self.call(self.files.read('/workspace/'+name,encoding='base64'))
        body=self.oracle[self.actor][name]+b'v'
        assert self.call(self.shell.execute('printf %s '+shlex.quote(body.decode())+' > /workspace/'+name)).exit_code==0
        self.oracle[self.actor][name]=body
        try:self.call(self.files.read('/workspace/'+name,expected_revision=previous['revision']))
        except ValueError:pass
        else:raise AssertionError('modified original accepted its old revision')

    @rule(old=st.sampled_from(('n0','n1','n2')),new=st.sampled_from(('n0','n1','n2')))
    def rename(self,old,new):
        if old==new or old not in self.oracle[self.actor]:return
        assert self.call(self.shell.execute('mv /workspace/'+old+' /workspace/'+new)).exit_code==0
        self.oracle[self.actor][new]=self.oracle[self.actor].pop(old)

    @rule(actor=st.sampled_from(('a','b')))
    def switch_actor(self,actor):
        self.close();self.actor=actor;self.open()

    @rule()
    def complete(self):
        self.close();self.store.complete(self.store.complete_step+1)
        self.frozen=deepcopy(self.oracle);self.open()

    @precondition(lambda self:self.store.complete_step>0)
    @rule()
    def restore(self):
        source=self.store.path
        self.close(save=False);self.store.close();self.branch+=1
        self.store=StageStore.restore(source,self.root/('run'+str(self.branch)))
        self.oracle=deepcopy(self.frozen);self.open()

    @invariant()
    def compare_actual_files_and_byte_ranges(self):
        page=self.call(self.files.ls('/workspace'))
        assert sorted(Path(item['path']).name for item in page.items)==sorted(self.oracle[self.actor])
        assert page.total==len(self.oracle[self.actor])
        for name,body in self.oracle[self.actor].items():
            whole=self.call(self.files.read('/workspace/'+name,encoding='base64'))
            assert base64.b64decode(whole['data'])==body
            piece=self.call(self.files.read('/workspace/'+name,offset=2,size=3,encoding='base64',expected_revision=whole['revision']))
            assert base64.b64decode(piece['data'])==body[2:5]

    def teardown(self):
        self.close(save=False);self.store.close();self.loop.close();self.temp.cleanup()


TestFilesMachine=FilesMachine.TestCase
TestFilesMachine.settings=settings(max_examples=12,stateful_step_count=20,deadline=None)
