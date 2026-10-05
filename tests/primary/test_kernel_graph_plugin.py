import json
import pytest
from examples.core_next.graph_environment import graph_plugin
from society0.kernel.composition import compose


@pytest.mark.asyncio
async def test_async_graph_source_and_numeric_projection_restore_without_source(tmp_path):
    source=tmp_path/'graph.json'
    source.write_text(json.dumps({'nodes':[{'id':'a','weight':1.25},{'id':'b','weight':2.5}], 'edges':[['a','b']]}))
    plugin=graph_plugin(source)
    async with compose(tmp_path/'run',[plugin]) as host:
        service=host.service('graph','graph');graph,values=service.projection()
        assert list(graph.edges)==[('a','b')] and values.tolist()==[1.25,2.5]
        service.set_weight('b',7.75)
        host.service('storage','store').complete(1)
    source.unlink()
    async with compose(tmp_path/'restored',[plugin],source=tmp_path/'run',step=1) as host:
        graph,values=host.service('graph','graph').projection()
        assert list(graph.edges)==[('a','b')] and values.tolist()==[1.25,7.75]
        assert graph.nodes['b']['weight']==7.75
