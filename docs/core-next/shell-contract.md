# Shell 交互合同

ShellSession 将可选 Bashkit 解释器接到同一共享环境的信息与动作服务。主体自己写入的 workspace 留在解释器文件系统，世界材料通过 data 命令按需取得。普通规则 Driver 可以直接调用交互服务，无需创建 shell。

## 一、入口

安装 `shell` extra 后，由运行时创建 `ShellSession(scope,information,actions,result_dir=...,workspace_snapshot=None,preview_bytes=65536)`。Driver 可改用 `ShellSession(scope,information,bound_actions=facade,result_dir=...)` 注入已绑定门面，actions 与 bound_actions 二选一，发现、描述与执行都经过同一门面。result_dir 指向本运行拥有的工件目录，每个会话建立独立子目录。可选依赖只在 `kernel.shell` 中导入。

```python
shell = ShellSession(scope, information, actions, result_dir=run_dir / 'shell')
result = await shell.execute('data list /')
workspace = shell.snapshot()
await shell.aclose()
```

execute 返回 ShellResult，包含 session_id、command_id、stdout、stderr、exit_code、error、两个输出的 truncated、total_bytes、next_offset 与文件引用，以及本命令产生的完整动作 receipts。传入的 result_dir 与会话限定的相对引用（如 `shell-abc/output/1.stdout`）共同定位工件。不同会话可以各有 command_id=1，完整引用仍有区别。运行存储复制封存这些工件后，外部研究者通过同一相对引用读取旧结果；机器绝对路径不进入结果身份。会话内 read_result 拒绝读取其他会话结果。Driver 可注入同步 `result_reader(reference, offset=0, size=65536, encoding="utf-8")`；`result read` 对旧会话的限定引用调用该门面，由 Thread 登记的主体归属与持久工件映射定位原文，当前会话引用仍读取当前结果文件。该读取保持 scope 有效性检查，且不会重新执行原动作。shell stdout/stderr 的合同是 Bashkit 产生的 UTF-8 文本。原生 printf 非法 UTF-8 字节可能在解释器内已变成替换字符；这不具备任意 GNU bash 二进制兼容性。二进制世界材料经 data read 的显式 base64 通路保持原字节。输出摘要按字节预算读取，边界上的 UTF-8 字符留给下一次读取；退出码与输出完整性分别呈现。

## 二、命令

data、action 和 result 是固定的自定义命令。其余 shell 能力使用 Bashkit 的普通命令和管道。参数中的 JSON 必须按 shell 规则引用；主体身份由 scope 绑定，没有 actor 参数。

```bash
data list /
data list /market '{"limit":100}'
data read /market/document '{"offset":0,"size":65536}'
data query /market/orders '{"fields":["id","price"],"limit":100}'
action find '{"target":{"namespace":"market","kind":"order","key":"one"}}'
action describe '{"name":"buy","target":{"namespace":"market","kind":"order","key":"one"}}'
action invoke '{"name":"buy","target":{"namespace":"market","kind":"order","key":"one"},"arguments":{"quantity":1}}'
```

data list `/` 返回经 discover 授权的挂载目录，条目包含 path 与 Ref，并有 total 和继续游标。普通目录由提供者返回对象条目；动作目标来自这些 Ref。find 返回轻摘要，describe 才提供参数 schema。分页、查询和权限继续遵循交互合同，查询语义由实际提供者定义。

data read 默认返回 UTF-8 文本，字段为 data、encoding、total_bytes、next_offset、revision、source。size 至少为 4 字节，响应解码完整字符并按实际消费字节推进 offset。显式指定 `encoding:"base64"` 可取得任意字节范围，保留非文本材料原文；没有以替换字符掩盖解码错误。

```bash
mkdir -p /workspace
printf '1\n2\n3\n' > /workspace/rows
cat /workspace/rows | head -n 1
tail -n 1 /workspace/rows
rm /workspace/rows
result read output/1.stdout '{"offset":65536,"size":65536}'
```

Python 的 `shell.read_result(reference,offset=0,size=65536,encoding='utf-8')` 与 result read 使用相同范围合同，响应带完整总字节数与继续偏移。数据命令结果可经 jq、head、tail 等处理。共享大材料没有挂载为 lazy 整文件；Bashkit 的原生 cat 大 lazy 文件会全量物化，该接口不承诺对其流式改造。

## 三、结果

每次脚本在同一解释器的命令组中执行，stdout 和 stderr 重定向到会话专属磁盘挂载。变量和 cwd 保持正常跨调用语义，完整输出不受 Bashkit 默认 stdout 摘要截断影响。预览结束后可按引用继续读取；结果引用不再执行原脚本。

动作完成时先把完整 ActionResult 写入独立 JSON receipt 并 fsync，再交给 shell 管道。即使管道筛掉动作结果或摘要被截断，原始结果仍能回读。Driver 接入时应在已绑定的动作门面统计真实领域调用、预算与 required/terminal，并在成功 terminal 后拒绝同一脚本继续调用领域动作；本次 receipt 保留完整事实。stdout 文本不承担终止判定。该门面接入在 Driver 阶段完成。文件写失败向上报告；动作已发生这一事实不会因此自动撤销或重试。接入完整运行时后，这些工件还需由持久化完成记录引用，目前 ShellSession 本身不发布检查点。

结果文件体积随实际输出增长，完整动作结果同时存在 receipt 和脚本输出时会产生两份正文。Core 没有把它们长期累积在 Python 列表中；解释器仍可能在单个 builtin 返回字符串时分配该字符串，这不构成任意大单次动作返回值的恒定内存保证。大数据消费者应使用已有分页、引用与范围读取。

## 四、生命周期

snapshot 保存私有 VFS、变量和 cwd，不打包输出挂载目录与共享 World。恢复时创建新的 ShellSession，重新绑定新的 scope 和服务，提供相同种类的文件系统挂载。已有结果工件由运行目录保留，工作区 snapshot 不代替 Thread 或结果历史。调用期间禁止 snapshot。

同一 session 的 execute 串行。aclose 取消并等待正在运行的命令以及已登记的异步回调，释放解释器；取消 execute 也会关闭该 session 并清理回调。关闭后新的执行、结果读取与 snapshot 抛出 ScopeClosed。scope 由运行时拥有，关闭 shell 不替运行时关闭其他交互入口。

错误参数与不可访问资源返回非零 shell 退出码。动作 handler 的执行异常向运行层传播，即使其 Python 异常类型为 ValueError；该命令后续动作不再执行。动作故障后的完整步骤失效、恢复和是否可以继续，由 Runtime 与存储合同决定。预期业务拒绝仍通过 ActionResult.rejected 返回。

## 五、证据

作者测试使用真实 Bashkit 0.18.2，位于 `tests/primary/test_kernel_shell.py`。首次缺失实现的失败在 `research/core-next/shell-product-red.txt`，root 目录发现失败在 `shell-root-list-red.txt`，动作异常与续读元数据的红测在 `shell-product-boundaries-red.txt`，联合绿测在 `shell-product-green.txt`。

覆盖共享信息与动作、拒绝 actor 覆写、文本与二进制分块、workspace 增删与管道、变量和 cwd 恢复、新 scope 权限、超大单次动作结果完整续读、stderr 与 exit、异步取消清理及动作异常阻止后续行动。完整 Driver、模型工具请求、检查点引用和跨进程恢复仍由后续组合测试验证。
