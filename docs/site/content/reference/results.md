# StepResult 与结果读取

`from society0.kernel.results import StepResult, Results, TableValue, DatasetTable` 定义阶段结果及读取入口。组合时登记 results_plugin，并将服务引用交给 Runtime；独立分析可用 `Results(StageReader(path))`。Results 借用你传入的存储，关闭由存储的所有者负责。

## 阶段返回什么

阶段返回 StepResult，Runtime 负责将它与实际激活结果一同保存。

<!-- api: society0.kernel.results.StepResult -->
<!-- api: society0.kernel.results.TableValue -->
<!-- api: society0.kernel.results.DatasetTable -->
<!-- api: society0.kernel.results.results_plugin -->

```python
return StepResult(metrics={'count': count}, tables={
    'events': rows,
    'configuration': TableValue(configuration),
})
```

片段位于阶段函数内。普通列表、生成器表示逐行表，逐行 JSON 编码并按有限行数分批事务写入；生成器会被消费一次。裸 Mapping、字符串、bytes 或 DataFrame 作为表输入会抛 `TypeError`，避免误把键或字符视为行。完整 JSON 值用 TableValue，保存为一条记录；DataFrame 可显式转换为 `TableValue(frame.to_dict(orient='tight'))`，转换成本随完整值大小增长。

DatasetTable(reference) 复用本运行已封存登记的数据集，其行数按数据集原记录数计入。需在组合中登记一次共享 dataset_plugin；普通行结果不需要冷数据集服务。

metrics、artifacts、observations 与 notes 保留完整值。artifacts 字段是结果元数据，实际文件仍须通过存储或 Session 的工件入口封存登记。写入失败使完整步骤失败；分批已写结果留作原运行诊断，恢复以完整描述符为准。

## 读取表、指标与记录

结果表引用与业务 JSON 分层，调用者按信封读取。

<!-- api: society0.kernel.results.Results.phase -->
<!-- api: society0.kernel.results.Results.metric -->
<!-- api: society0.kernel.results.Results.page -->
<!-- api: society0.kernel.results.Results.read_record -->
<!-- api: society0.kernel.results.Results.step -->
<!-- api: society0.kernel.results.Results.summary -->

```python
header = results.phase(10, 0)
reference = header['tables']['events']
page = results.page(reference, limit=100, max_bytes=65536)
for item in page['items']:
    if 'value' in item:
        consume(item['value'])
    else:
        chunk = results.read_record(item['payload_ref'])
```

consume 是应用自己的处理函数；大记录继续沿 chunk['next_offset'] 调用 read_record，取得全部 JSON 字节后再解码。页项为 `{ordinal, raw_bytes, value}` 或 `{ordinal, raw_bytes, payload_ref}`；业务字段全部位于 value 内，业务值里的同名键不会被误识别为引用。

page 返回精确 total 与 next_cursor。普通结果集首次读取固定 through 上界，后续页保持该范围，并带 finished 标志；冷数据集页使用相同页项结构但没有普通结果集的 finished 字段。limit 要求正整数，max_bytes 至少 512，字节预算包含页项和分页外壳。预算无法容纳引用时抛 `ValueError`。恢复后 run_id 改变，旧分页游标失效，应从第一页重查。

phase 的身份是 `(step, phase_index)`，同一步可含重复名称阶段。metric 按阶段名读取最新投影，下一次同名阶段返回时替换指标键集合；缺项抛 `KeyError`。phase 与 metric 会明确物化所选完整值，大字段宜使用表与范围引用。

## 状态与成本

结果事实、运行完整身份与进度需分别解释。

summary 读取规范写入时维护的计数，不重放历史。step 返回该次尝试保存的时间、阶段数、激活数与执行参数；其正式完成仍由存储描述符确认。成功返回的激活保留 actor、round、status、reason、value，显式 collect 的 incomplete 保持原状态。异常或取消的阶段沿 Thread、失败标记与进度追溯。

分页成本随所读行和正文块增长；大行范围读取按偏移定位块，全文读取承担整条值成本。

见[结果教程](<../tutorial/results.md>)；源码见 [Results.page](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/results.py#L167>)。
