import asyncio, subprocess, types, sys, tempfile
from pathlib import Path
from society0.kernel import results as current
from society0.kernel.storage import StageStore
source=subprocess.check_output(['git','show','f6f5646affb7abf742ed70f33aa5b48265e1e904:src/society0/kernel/results.py'],text=True)
module=types.ModuleType('society0.kernel._result_shape_baseline')
sys.modules[module.__name__]=module
exec(compile(source,'HEAD:results.py','exec'),module.__dict__)
# 编码接口正由另一独立任务替换；保留其当前实现，隔离验证输入迭代语义。
module.Results._row=staticmethod(current.Results._row)
async def main():
    with tempfile.TemporaryDirectory() as directory:
        with StageStore.create(Path(directory)/'run',module.RESULTS_SCHEMA) as store:
            result=module.Results(store)
            header=await result.write_phase(1,0,'analysis',module.StepResult(tables={'value':{'column':[1,2]}}))
            actual=result.page(header['tables']['value'])['items']
            print('baseline mapping input:',actual)
            assert actual==[{'column':[1,2]}], 'mapping silently became column names'
asyncio.run(main())
