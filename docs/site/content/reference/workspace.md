# WorkspaceStore 与 Lease

`from society0.kernel.workspace import workspace_plugin, WorkspaceStore, WorkspaceLease` 用共享 SQL 索引记录主体私有文件的位置，把文件正文放进不可变工件。每次激活打开一个短生命周期的 lease，由打开它的调用者调用 close 结束；lease 借用的 StageStore 和 scope 仍归创建者管理，文件能否恢复由完整步骤发布的身份决定。

## 装配与读取

通常将 workspace_plugin 加入 compose，再把 `workspace.workspace` 服务传给 ShellSession 使用。

<!-- api: society0.kernel.workspace.workspace_plugin -->
<!-- api: society0.kernel.workspace.WorkspaceStore -->
<!-- api: society0.kernel.workspace.WorkspaceStore.open -->
<!-- api: society0.kernel.workspace.WorkspaceLease.read_range -->
<!-- api: society0.kernel.workspace.WorkspaceLease.list_files -->

WorkspaceStore 要求 Actor schema 与它同库，工厂通过 schema_requires 声明这一数据前提，安装时只借用 StageStore。open 会检查 scope 和主体身份，返回带原 shell 状态及根元数据的 lease。主体休眠后，它的 SQL 记录和工件继续保留，不需要常驻解释器。

read_range 返回 `DocumentChunk`，其中带原始字节、总长度、下一偏移、revision 与 `Ref`；list_files 返回 `Page`，总数是精确值。路径都是工作区内的路径，例如 `/note.txt`。游标绑定主体、路径和 workspace_files 版本，数据变化之后旧游标或 expected_revision 会被拒绝。

## 保存与恢复

lease 把原生 overlay 的最终变更提交给所属主体，可恢复身份由完整步骤发布。

<!-- api: society0.kernel.workspace.WorkspaceLease.save -->
<!-- api: society0.kernel.workspace.WorkspaceLease.close -->

save 先为发生变化的文件准备工件，再在同一个事务里应用删除、最终条目和 shell-only 状态，并登记工件依赖。正文没有改动时复用原来的引用；下一次激活直接读取当前索引。文件级增量保留权限、时间和符号链接目标，持久 FIFO 会被拒绝。符号链接遵循解释器的 inert 语义，可以读取它指向目标的文本。

提交失败会让当前步骤无法完成，恢复时从此前可信的完整步骤取得文件索引与工件。工作区跨 Moment 保存，临时解释器文件按每次激活重建。关闭 lease 会取消并等待它的回调，关闭之后再去访问会失败。

## 资源与实例

按路径读取的开销取决于选中的文件；范围读取按请求的字节数物化。原生 overlay 修改一个文件时可能整文件 copy-up，大文件频繁追加仍然要承担完整复制的开销。显式的完整目录回调随目录条目数增长；分页接口按本页索引读取。

```python
from society0.kernel.workspace import workspace_plugin

plugin = workspace_plugin(name="workspace", actors="actors.data")
# 在含 storage 和 actors.data 的组合中安装；ShellSession 借用其 workspace 服务。
```

日常使用见[工作区指南](../guide/workspace.md)，保存调用见[ShellSession](shell.md)。实现为 [WorkspaceLease.save](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/workspace.py#L107)。
