# 封存不可变资料与结果引用

大批原始资料可以封存为有序数据集，领域表保存归属和当前引用。这样常规业务查询访问短记录，需要正文时再按页或字节读取。先完成[基础安装](../start/installation.md)；本页的封存检查无需模型、向量服务或 Shell。

## 一、创建一批资料

保存以下完整程序为仓库根目录的 `catalog_check.py`。它复用仓库的 Catalog 示例，把正文与拥有者引用一起登记。

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

预期总数为 2，两次读取分别返回一行。该程序检查封存与分页；在正式研究中，由机制的阶段或行动调用 replace，再由 Runtime 发布完整步骤。

## 二、保存引用与授权

[Catalog.replace](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/immutable_catalog.py#L12-L20) 调用 `Datasets.import_rows`，并通过 attach 回调在规范写入事务中更新拥有者与当前引用。资料已经封存后，后续业务改变当前引用即可保留原批次的身份。

数据集存储负责原始有序记录，主体能否发现和读取由领域 Information 提供者决定。示例的 Catalog 按 owner 核对主体，调用者若要挂入路由，还需实现该研究所需的目录发现和文档入口。不要把底层 datasets 服务直接当成已经授权的主体视图；可参照[SQL 信息指南](information.md)建立完整入口。

## 三、读取大行并转交结果

页项区分直接内联的值与大行原文引用。读取大行时用 `read_payload` 的 offset/size 按字节拼接，最后解码完整 JSON；页大小控制记录数量，单行大小仍可能很大。接口和返回信封见[数据集参考](../reference/cold-datasets.md)。

结果服务可以接收已有引用，避免再次把整批资料转成 Python 列表。以下为阶段返回片段，`reference` 来自同一运行中已封存的数据集：

```python
from society0.kernel.results import DatasetTable, StepResult

result = StepResult(tables={"source_material": DatasetTable(reference)})
```

随后沿[结果教程](../tutorial/results.md)取得阶段表引用并分页读取。工作台导出仍会物化明确选择的结果范围，引用转交不消除展示端取得全部正文的成本。

## 四、核对恢复后的资料

在完整步骤之后保存批次引用，从该点建立新运行，比较行数、顺序、中文原文和领域归属。测试某批资料替换后，旧完整点仍能定位它当时的批次。大量导入成本发生在封存时；热查询尽量读取当前引用和索引，避免每步重读全部历史。

需要转移运行时采用[存储导出与恢复](storage.md)，让不可变工件随完整身份一起保存；孤立复制当前数据库可能遗漏资料依赖。不同运行之间的引用转交须遵守其来源与导出合同。
