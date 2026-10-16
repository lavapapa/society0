# WorkspaceStore 与 Lease

`from society0.kernel.workspace import workspace_plugin, WorkspaceStore, WorkspaceLease`。工作区用共享 SQL 索引定位主体私有文件，以不可变工件保存正文；每次激活打开短生命周期 lease。

## 一、工厂与读取

通常将 workspace_plugin 加入 compose，再把 `workspace.workspace` 服务传给 ShellSession。

<!-- api: society0.kernel.workspace.workspace_plugin -->
<!-- api: society0.kernel.workspace.WorkspaceStore -->
<!-- api: society0.kernel.workspace.WorkspaceStore.open -->
<!-- api: society0.kernel.workspace.WorkspaceLease.read_range -->
<!-- api: society0.kernel.workspace.WorkspaceLease.list_files -->

WorkspaceStore 要求同库 Actor schema；工厂通过 schema_requires 声明数据前提，安装只借用 StageStore。open 检查 scope 和主体身份，返回带原 shell 状态及根元数据的 lease。休眠主体保留 SQL 与工件，无需常驻解释器。

read_range 返回 DocumentChunk，携带原始字节、总长度、下一偏移、revision 与 Ref；list_files 返回含精确总数的 Page。路径为工作区内路径，例如 `/note.txt`。游标绑定主体、路径和 workspace_files 版本，数据变化后拒绝旧游标或 expected_revision。

## 二、保存与恢复

Lease 将原生 overlay 的最终变更提交到所属主体，完整步骤负责发布可恢复身份。

<!-- api: society0.kernel.workspace.WorkspaceLease.save -->
<!-- api: society0.kernel.workspace.WorkspaceLease.close -->

save 先准备改变文件的工件，再在单事务应用删除、最终条目及 shell-only 状态，并登记工件依赖。未改正文复用原引用；下一次激活直接读取当前索引。文件级增量保留权限、时间和符号链接目标，持久 FIFO 被拒绝。符号链接遵循解释器的 inert 语义，可读取其目标文本。

提交失败使当前步骤无法完成，恢复从此前可信完整点取得文件索引与工件。工作区跨 Moment 保存，临时解释器文件按激活重建。关闭 lease 取消并等待其回调，借用的 StageStore 和 scope 继续由创建者管理；关闭后的访问失败。

## 三、资源与实例

按路径读取成本取决于所选文件；范围读取按请求字节物化。原生 overlay 修改一个文件时可能整文件 copy-up，大文件频繁追加仍有完整复制成本。显式完整目录回调随目录条目数增长；分页接口按本页索引读取。

```python
from society0.kernel.workspace import workspace_plugin

plugin = workspace_plugin(name="workspace", actors="actors.data")
# 在含 storage 和 actors.data 的组合中安装；ShellSession 借用其 workspace 服务。
```

日常使用见[工作区指南](../guide/workspace.md)，保存调用见[ShellSession](shell.md)。实现为 [WorkspaceLease.save](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/workspace.py#L107)。
