# ShellSession

`from society0.kernel.shell import ShellSession, ShellResult`。ShellSession 将可选 Bashkit 解释器接入同一主体的 Information、Actions 和私有工作区，适合文件处理、管道及结构化查询。

## 一、调用

构造时提供 actions 或 bound_actions 二者之一；LLMDriver 使用绑定账本门面，使脚本内部行动服从同一预算和完成条件。

<!-- api: society0.kernel.shell.ShellSession -->
<!-- api: society0.kernel.shell.ShellSession.execute -->
<!-- api: society0.kernel.shell.ShellResult -->
<!-- api: society0.kernel.shell.ShellSession.read_result -->

execute 返回 ShellResult，包含退出码、stdout/stderr 预览、完整字节数、继续偏移、输出引用及完整行动回执引用。每个会话拥有 result_dir 下的独立子目录；会话限定的相对引用避免不同 command_id 冲突。

`data list/read/query` 消费授权资料，`action find/describe/invoke` 发现并执行领域行动，`result read` 继续读取输出。JSON 参数按 shell 规则引用，主体身份由 scope 确定。普通路径从 `/world`、`/context`、`/workspace` 和 `/results` 发现。

## 二、状态与失败

同一会话的 execute 串行，变量和 cwd 跨命令保留。接入持久 WorkspaceStore 时初始 cwd 为 `/workspace`，该目录保存私有文件；临时目录随本次解释器生命周期结束。workspace_snapshot 与持久 workspace 互斥。

错误参数、不可访问资料返回非零退出码；领域 handler 异常传播到运行层，并阻止当前命令后续行动。预期业务拒绝通过 ActionResult 返回。成功 terminal 行动后，同一脚本继续调用领域动作会被拒绝。

完整回执先写入并同步到文件，再进入管道。输出被筛选或预览截断时，原始回执仍可读取。文件写入失败会传播，已发生的外部行动需按运行恢复合同处理。ShellSession 不自行发布完整步骤；Driver 登记封存工件与 Thread 归属。

## 三、保存与资源

工作区在解释器空闲时保存，随后关闭解释器与回调。

<!-- api: society0.kernel.shell.ShellSession.save_workspace -->
<!-- api: society0.kernel.shell.ShellSession.aclose -->

save_workspace 保存改变文件、删除路径和 shell 状态；修改大文件可能触发整文件复制。aclose 取消并等待命令和回调，释放解释器；取消 execute 会关闭该会话。关闭后执行、结果读取及快照操作失败，原运行 scope 的其他入口仍由 Runtime 管理。

普通 cat/head 等可能整读原文，模型专用 read 使用范围通路。stdout/stderr 遵循 Bashkit 的 UTF-8 文本合同；精确二进制资料通过 data read 的 base64 通路读取。完整输出和回执占用磁盘，单个 builtin 仍可能物化完整字符串。

以下为已有 scope 与资源上的调用片段：

```python
async def write_note(scope, information, actions, workspace, result_dir):
    shell = ShellSession(scope, information, actions,
                         workspace=workspace, result_dir=result_dir)
    try:
        result = await shell.execute("printf '观察记录\\n' > /workspace/note.txt")
        shell.save_workspace()
        return result
    finally:
        await shell.aclose()
```

更多操作见[工作区指南](../guide/workspace.md)和[工作区接口](workspace.md)。实现入口为 [ShellSession](https://github.com/lavapapa/society0/blob/@COMMIT@/src/society0/kernel/shell.py#L60)。
