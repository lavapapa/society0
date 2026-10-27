# 封存不可变资料与结果引用

手里的原始资料很多、写进去之后又不再改动时，可以把它封存成有序数据集，领域表只保存归属和当前引用。日常业务查询因此只碰短记录，需要正文时再按页或按字节去取。先完成[基础安装](../start/installation.md)；下面的封存检查不需要模型、向量服务或 Shell。

## 封存一批资料并分页读回

把下面的完整程序保存为仓库根目录的 `catalog_check.py`。它复用仓库的 Catalog 示例，把正文和拥有者引用一起登记。

```python title="catalog_check.py"
import asyncio
import sys
from society0 import compose
from society0.plugins import dataset_plugin
from examples.core_next.immutable_catalog import catalog_plugin


async def main(path):
    async with compose(path, (dataset_plugin(), catalog_plugin())) as host:
        catalog = host.service("catalog", "catalog")
        datasets = host.service("datasets", "datasets")
        reference = catalog.replace("alice", (
            {"id": 1, "text": "第一份原始资料。"},
            {"id": 2, "text": "第二份原始资料。"},
        ))
        first = datasets.page(reference, limit=1)
        second = datasets.page(reference, limit=1, cursor=first["next_cursor"])
        assert first["total"] == 2
        assert second["next_cursor"] is None
        print({"total": first["total"], "first": first["items"], "second": second["items"]})


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
```

```bash
uv run python catalog_check.py runs/catalog-check
```

预期总数为 2，两次读取各返回一行。这个程序检查的是封存和分页本身；正式研究里由机制的阶段或行动调用 `replace`，再让 Runtime 发布完整步骤。

## 保存引用与授权

[Catalog.replace](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/immutable_catalog.py#L12-L20) 调用 `Datasets.import_rows`，并通过 attach 回调在规范写入事务里更新拥有者和当前引用。资料封存之后，后续业务只要改当前引用，就能保住原批次的身份。

数据集存储负责原始的有序记录，主体能不能发现、能不能读，由领域 Information 提供者决定。示例里的 Catalog 按 owner 核对主体；调用者要把资料挂进路由，还得自己实现这项研究需要的目录发现和文档入口。别把底层的 datasets 服务直接当成已经授权的主体视图，可以参考[SQL 信息指南](information.md)建一个完整的入口。

## 读取大行并转交结果

页项会区分直接内联的值和大行原文引用。读大行时用 `read_payload` 的 offset/size 按字节拼起来，最后解码成完整 JSON；页大小控制的是记录条数，单行本身仍然可能很大。接口和返回信封见[数据集参考](../reference/cold-datasets.md)。

结果服务可以直接接收已有的引用，不必再把整批资料转成 Python 列表。下面是阶段返回的片段，`reference` 来自同一次运行里已经封存的数据集：

```python
from society0.kernel.results import DatasetTable, StepResult

result = StepResult(tables={"source_material": DatasetTable(reference)})
```

随后按[结果教程](../tutorial/results.md)取得阶段表的引用并分页读取。完整导出会物化所选范围，成本随原文大小增长。

## 核对恢复后的资料

完整步骤发布之后保存批次引用，从这个点建立新运行，再比较行数、顺序、中文原文和领域归属。测试替换某一批资料之后，旧的完整步骤应该仍能定位到它当时那一批。大量导入的开销发生在封存那一刻；热路径上的查询尽量读当前引用和索引，不要每一步都把全部历史重读一遍。

要转移运行时，走[存储导出与恢复](storage.md)，让不可变工件跟着完整身份一起保存；只把当前数据库复制一份，可能漏掉资料依赖。不同运行之间转交引用时，按来源运行和导出时的要求处理。
