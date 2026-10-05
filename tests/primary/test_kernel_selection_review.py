"""主体选择与聚合的非作者消费者。"""
import pytest
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.composition import compose
from society0.kernel.selection import select_ids,sample_ids,result_rows,result_mean
from society0.kernel.runtime import ActorResult,DriverResult


@pytest.mark.asyncio
async def test_review_selector_crosses_pages_without_drivers_and_rejects_changed_membership(tmp_path):
    def forbidden(record):raise AssertionError('selection constructed a driver')
    plugin=actor_plugin({'rule':forbidden},records=[ActorRecord(str(i),'rule',roles=('r',)) for i in range(251)])
    async with compose(tmp_path/'run',[plugin]) as host:
        actors=host.service('actors','actors')
        assert list(select_ids(actors,role='r'))==[str(i) for i in range(251)]
        source=select_ids(actors,role='r')
        assert next(source)=='0'
        actors.deactivate('250')
        with pytest.raises(ValueError,match='cursor'):
            list(source)


def test_review_zero_sample_does_not_consume_and_streamed_values_keep_identity():
    def forbidden():
        raise AssertionError('zero sample consumed input')
        yield None
    assert sample_ids(forbidden(),0,seed=1)==[]
    values=[{'score':2,'payload':bytearray(b'full')},{'score':4}]
    records=[ActorResult('a',index,DriverResult('completed',value)) for index,value in enumerate(values)]
    rows=list(result_rows(iter(records)))
    assert all(row['value'] is value for row,value in zip(rows,values))
    assert result_mean(iter(records),'score')==3
