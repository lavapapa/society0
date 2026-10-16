# 恢复、分析与清理

`from society0.kernel.storage import StageStore, StageReader` 提供从完整点生成新目录的入口。选择继续运行或只读分析后，再决定使用哪种物化方式。

## 一、可写恢复

restore 同时承担独立导出与完整链压实。

<!-- api: society0.kernel.storage.StageStore.restore -->

```python
with StageStore.restore(source, destination, step=10) as store:
    assert store.complete_step == 10
```

source 是完整运行，destination 必须尚不存在；step 省略时选最新完整身份。返回 StageStore，调用者拥有写者资源并负责关闭。fork 是 restore 的同一入口别名。显式 run_id 必须与源不同；默认建立新身份，清单保存来源 run_id 与 step。

恢复从 root 应用选中完整链，不读取 dirty current 的业务数据。源 root、schema、描述符身份、变化文件及工件缺失或不一致时失败；目标在完整构建前位于临时目录，失败不发布 destination。完整点选择须同时满足研究状态可信，能读取的完整点仍需按业务口径判断。

新 root 起于所选步骤，之后可以继续运行、分叉和再次恢复。SQL 中已有 Thread、记忆和业务历史保留；此前逐步恢复身份被压实。源删除后目标可独立使用。工件同文件系统用硬链接建立独立目录入口，跨设备 EXDEV 才复制，其他错误传播；任何封存文件均不得原地修改。

成本包含恢复链应用与 current/new root 的全库复制。累计 SQL 历史仍占空间，完整历史曾引用的工件继续保留；压实链长与减少历史事实是两件独立的操作。

## 二、只读准备

固定完整点分析使用 prepare_readonly，可省去新可写 root。

<!-- api: society0.kernel.storage.StageStore.prepare_readonly -->

```python
with StageStore.prepare_readonly(source, analysis_dir, step=10) as reader:
    rows = reader.read(lambda view: view.query('SELECT id FROM actors'))
```

返回 StageReader。新目录具有新的读取身份并保存 source 身份及所选 step；关闭 reader 后目录和已登记工件继续存在，可由 StageReader 再次打开。分析目录可脱离源运行读取结果、Thread、权威记忆及冷数据集。

**只读准备目录不能作为 writer 打开，也不能作为 restore 或再次 prepare_readonly 的源。** 继续仿真应从原完整运行选择 restore。准备期间源后来产生的未完成事实不进入视图。准备成本仍随选中完整点的状态与工件增长，之后的读取沿对应分页与范围接口进行。

## 三、离线清理

collect_orphans 处理已明确留存诊断之后的准备孤儿。

<!-- api: society0.kernel.storage.StageStore.collect_orphans -->

调用前停止该运行的 writer。接口先取得独占锁，验证 root、完整链和全部引用；活动 writer 或组件错误会在删除前抛 StorageError。随后删除 artifacts、changesets 中未被任何保留完整身份引用的文件，返回实际删除的相对路径。

root、current、完整描述符及其他目录保留。未完成步骤的诊断工件若尚无完整引用，会进入清理范围；请先保留需要分析的证据。扫描成本随保留历史和工件数量增长，应安排为离线维护。此操作不识别插件当前指针，不做业务历史裁剪。

继续阅读[恢复教程](<../tutorial/recovery.md>)与[完整步骤概念](<../concepts/complete-steps.md>)；源码见 [restore](<https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/storage.py#L622>)。
