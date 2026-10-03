"""可选 pandas 消费者：显式 tight 值经结果服务与完整恢复。"""
import asyncio
import tempfile
from pathlib import Path
import pandas as pd
from society0.kernel.results import Results, RESULTS_SCHEMA, StepResult, TableValue
from society0.kernel.storage import StageStore

async def main():
    frame=pd.DataFrame({'name':['甲','乙'],'amount':pd.Series([2**90,-3],dtype=object)})
    frame.index=pd.Index([10,'10'],name='主体')
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)
        with StageStore.create(path/'run',RESULTS_SCHEMA) as store:
            results=Results(store)
            await results.write_phase(1,0,'analysis',StepResult(tables={'table':TableValue(frame.to_dict(orient='tight'))}))
            store.complete(1)
        with StageStore.restore(path/'run',path/'restored') as store:
            results=Results(store)
            value=results.page(results.phase(1,0)['tables']['table'])['items'][0]
            pd.testing.assert_frame_equal(pd.DataFrame.from_dict(value,orient='tight'),frame)
    print('pandas tight value: columns, index, order, large integers and restore equal')

if __name__=='__main__':asyncio.run(main())
