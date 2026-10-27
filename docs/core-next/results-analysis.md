# 分页读取完整结果

本指南使用纯 Python 读取已发布完整步骤的指标、表行和大正文。先完成[运行与恢复](getting-started.md)，再将代码中的 `run_dir` 改成恢复目录；默认路径为 `$SOCIETY0_STUDY/environment-check/resumed`。`step` 选择完整步骤，`table_name` 选择阶段结果头部中实际存在的表。

## 一、读取

程序逐页遍历阶段和结果；大行沿 `payload_ref` 读取全部 JSON 原文字节，保留阶段序号。

```python
import base64
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from society0.kernel.observation import Observation
from society0.kernel.storage import StageStore

run_dir = Path(os.environ['SOCIETY0_STUDY']) / 'environment-check' / 'resumed'
step = 2
table_name = "decisions"


def values(reader, reference):
    cursor = None
    while True:
        page = reader.result_page(reference, cursor=cursor)
        for item in page['items']:
            if 'value' in item:
                yield item['value']
            else:
                body = bytearray()
                offset = 0
                while True:
                    part = reader.read_result_record(item['payload_ref'], offset=offset)
                    body.extend(base64.b64decode(part['data']))
                    offset = part['next_offset']
                    if offset is None:
                        break
                yield json.loads(body)
        cursor = page['next_cursor']
        if cursor is None:
            break


metric_rows, table_rows = [], []
with TemporaryDirectory() as temporary:
    view = Path(temporary) / 'complete'
    with StageStore.prepare_readonly(run_dir, view, step=step):
        pass
    with Observation(view) as reader:
        cursor = None
        seen = 0
        while True:
            phases = reader.result_phases(step=step, cursor=cursor)
            for phase in phases['items']:
                identity = {'step': step, 'phase': phase['name'], 'phase_index': phase['ordinal']}
                header = next(values(reader, phase['reference']))
                metric_rows.extend({**identity, **metric} for metric in values(reader, header['metrics']))
                if table_name in header['tables']:
                    table_rows.extend({**identity, 'table': table_name, 'value': value}
                                      for value in values(reader, header['tables'][table_name]))
            seen += len(phases['items'])
            if seen == phases['total']:
                break
            cursor = phases['cursor']

print('指标：', metric_rows)
print('表行：', table_rows)
```

## 二、使用结果

`value` 保留表行的原始 JSON，包括空值、布尔值、数组和对象。需要 DataFrame 时，在分析环境安装 pandas，再将 `metric_rows` 或 `table_rows` 传给 `pandas.DataFrame`。大型分析可逐页消费 `values`，避免把全部行同时放进列表；完整单值仍有相应物化成本。

更多返回字段见[Results 参考](../site/content/reference/results.md)，实时诊断见[Observation 参考](../site/content/reference/observation.md)。运行、读取与恢复均可通过 Python 完成。
