"""非作者：正式资源预检与显式记忆消费者，避免 fake 资源掩盖装配错误。"""
import pytest
from tests.primary.test_skill_starter import load_starter,resources
from society0.kernel.runner import run_plan
from society0.kernel.storage import StageReader


@pytest.mark.asyncio
async def test_review_starter_actual_resource_preflight_never_calls_network(tmp_path,monkeypatch):
    from society0.kernel.models import ModelProvider,EmbeddingProvider
    async def forbidden(*args,**kwargs):raise AssertionError('preflight attempted provider')
    monkeypatch.setattr(ModelProvider,'request',forbidden)
    monkeypatch.setattr(ModelProvider,'request_model',forbidden)
    monkeypatch.setattr(EmbeddingProvider,'embed',forbidden)
    endpoint={'id':'test','model':'test','base_url':'http://unused.invalid/v1','api_key':'unused','concurrency':1}
    plan=load_starter().build_plan(release={'commit':'test'},moments=(),
        model={'endpoints':[endpoint]},embedding={'endpoints':[endpoint],'dimensions':2})
    await run_plan(tmp_path/'check',plan)
    with StageReader(tmp_path/'check') as reader:
        assert reader.read(lambda r:r.complete_step)==0
        assert reader.read(lambda r:r.query('SELECT count(*) FROM memory_jobs'))==[(0,)]


@pytest.mark.asyncio
async def test_review_starter_explicit_experience_saved_once_before_interview(tmp_path):
    requests=[]
    await run_plan(tmp_path/'run',load_starter().build_plan(release={'commit':'test'},resource_plugins=resources(requests)))
    with StageReader(tmp_path/'run') as reader:
        assert reader.read(lambda r:r.complete_step)==2
        assert reader.read(lambda r:r.query('SELECT actor,timestamp,state FROM memory_rows'))==[('alice',1,'ready')]
        assert reader.read(lambda r:r.query('SELECT count(*) FROM memory_jobs'))==[(1,)]
    interview=requests[-1]
    assert '我读到尚无官方确认的地铁消息' in str(interview)
