# 主体文件视图

`society0.kernel.actor_files.ActorFiles` 将当前 Session 的环境资料、认知、工作区和结果统一为逻辑路径。`society0.kernel.information_fs.InformationFiles` 为 Shell 提供 Information 的动态文件投影。主体身份从作用域绑定。

## 一、公开入口

LLMDriver 已管理 ActorFiles，定制消费者可在有效 Session 内直接使用。

<!-- api: society0.kernel.actor_files.ActorFiles -->
<!-- api: society0.kernel.actor_files.ActorFiles.ls -->
<!-- api: society0.kernel.actor_files.ActorFiles.read -->
<!-- api: society0.kernel.actor_files.ActorFiles.find -->
<!-- api: society0.kernel.actor_files.ActorFiles.grep -->
<!-- api: society0.kernel.information_fs.InformationFiles -->

从 `/` 发现四类路径：`/world` 为授权环境资料，`/context` 为本次认知及扩展材料，`/workspace` 为主体私有文件，`/results` 为本主体已登记输出。文件路径要求绝对路径且不含父目录跳转；世界事实修改通过 Actions 执行。

## 二、返回与版本

ls 返回 Page，含 items、精确 total、next_cursor 和 revision。read 返回 data、encoding、total_bytes、next_offset、revision 与来源；offset 和 size 使用字节语义，文本续读按实际消费字节推进。二进制选择 base64，解码错误会明确失败。后续原文段携带同一 expected_revision，版本变化时重新定位。

find 按逻辑路径 glob 查找，grep 扫描原始文本并保存完整结果工件，预览外的命中可以继续读取。搜索固定相关数据及授权水位；相关来源变化时抛错，未通过最终版本核验的搜索不返回成功结果。权限变更后新读取按当前资格执行；已取得的私有结果正文可依主体归属回读，尚未物化的 document_ref 继续核验源权限和版本。

主动记忆开启时 `/context/memory/records/` 可发现当前可见记忆，包括未召回条目；关闭主动访问会关闭该目录，已有召回材料与 Thread 历史继续保留。

## 三、读取成本

原文路径保持文件身份，`@manifest.json`、文本片和字节片属于显式传输入口；目录续页沿 `@page-` 发现。专用 read 按范围读取，grep 通过连续 Reader 逐文档扫描，成本随选定原文字节数增长，长行可能增加缓冲。

Shell 的 cat、head 等可整读选定原文；输出短不减少这一输入成本。大集合筛选使用 `data query` 下推到注册提供者。关闭 ActorFiles 会关闭其临时材料与文件投影；访问要求 Session scope 持续有效。

下面的辅助函数读取发现目录；由当前激活提供 files：

```python
async def visible_roots(files):
    page = await files.ls("/")
    return page.items
```

操作步骤见[文件教程](../tutorial/files.md)和[工作区指南](../guide/workspace.md)。源码入口为 [ActorFiles](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/actor_files.py#L38)。
