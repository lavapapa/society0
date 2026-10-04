# Shell 与信息视图选型试验

本文保留选型阶段的试验条件、测量和当时结论。文中的“当前”“候选”“尚未实现”均指该次试验时点；现行接口与装配方式见[Shell 合同](shell-contract.md)、[工作区合同](workspace-contract.md)与[安装说明](installation.md)。阶段状态与后续验收由 [TODO](TODO.md) 记录。

本次于 2026 年 10 月 4 日在临时虚拟环境试用 Bashkit 0.18.2，验证 Python Core 能否通过成熟 shell 实现连接共享信息服务与主体私有工作区。结论是优先采用 Python 调用原生 Rust Bashkit 的小适配层；共享 World 通过有界自定义命令读取，工作区存放主体主动生成的文件。产品依赖尚未修改，实验没有连接真实 World 或模型服务。

## 一、证据

### 1.1 版本与范围

Bashkit 发行版对应官方 v0.18.2 提交 `567511386572d4c4b9f649ab1da97aad53b9f341`，PyPI 轮子安装在 `/tmp/society0-shell-study-venv`，Python 3.13.0，macOS arm64。其[官方 Python 绑定源码](https://github.com/everruns/bashkit/blob/567511386572d4c4b9f649ab1da97aad53b9f341/crates/bashkit-python/src/lib.rs)核实了懒加载物化、回调和 GIL 行为。研究时 main 为 `dc6d6a139a6f956298b70152db83d50b5b42863e`，测试数字均归属于安装的 0.18.2。

对照 just-bash 固定提交 `7537a260e38648e8998db7a95504102ad80b0194` 的 [IFileSystem](https://github.com/vercel-labs/just-bash/blob/7537a260e38648e8998db7a95504102ad80b0194/packages/just-bash/src/fs/interface.ts)与[内存文件系统](https://github.com/vercel-labs/just-bash/blob/7537a260e38648e8998db7a95504102ad80b0194/packages/just-bash/src/fs/in-memory-fs/in-memory-fs.ts)。本轮没有执行 Node 对照基准，其性能优劣保持未测状态。

### 1.2 可重跑工件

`benchmarks/core_next_shell.py` 提供 pipelines、lazy、builtin、activations、transfer 五种独立进程探针。`tests/experiments/test_core_next_shell.py` 有五项通过，记录在 `research/core-next/shell-tests.txt`；未安装 Bashkit 的主仓环境跳过该试验模块。临时环境未安装 pytest-asyncio，出现一个仓库 asyncio_mode 未识别警告；探针使用标准库 asyncio.run，五项实际执行完成。

执行方法为先在独立环境安装 `bashkit==0.18.2` 与 pytest，然后以该环境 Python 执行 `benchmarks/core_next_shell.py lazy` 等命令。原始结果为 `research/core-next/shell-{mode}.json`。实验最初未复原 filesystem 初始化配置导致 snapshot 恢复失败，其记录保留在 `shell-snapshot-backend-observation.txt`；随后按发行版要求提供相同配置，工作区恢复成功。

## 二、行为

这一部分区分已经运行的行为与源码推断，以便后续适配器保留完整信息和权限语义。

### 2.1 管道、命令与主体视图

cat、head、tail 与 jq 管道都已执行。`custom_builtins={"data": callback}` 接受异步 Python 回调，`ctx.argv` 提供命令参数，返回字符串进入普通 shell 管道；`data query /visible | jq .total` 与 `data read /visible/allowed | jq -r .content` 已验证。试验服务返回固定 revision、total 与完整小内容，验证的是调度接通，权限与分页的真实合同仍需 Core 消费者测试。

适配器在创建激活实例时，把主体身份、固定信息视图与服务绑定在回调闭包中。query 返回精确 total、游标与有界记录，read 接受引用、offset 和 max_bytes；后续页继续使用同一 revision。命令参数中的主体标识没有授予权限的作用。shell 的数据来源仍为同一环境；主体私有工作区与可见信息服务分工明确。

### 2.2 懒加载与大输出

8 MiB 字符串懒文件的 `head -n 1` 输出 2 字节，加载器调用一次并返回完整字符串。首读约 6.22 ms，重复约 2.93 ms；进程 RSS 从 32.54 MB 升至 60.60 MB，释放实例后约 60.70 MB。该 RSS 包含分配器、解释器和缓存，释放对象后常驻页未立刻归还操作系统，数值不能解释为永久泄漏或精确对象尺寸。源码的 `PythonLazyFilesFs.materialize_if_needed` 从 Python str 转为 Rust Vec，再写入 overlay，随后 read_file 返回整份字节；lazy 推迟读取，首次使用仍整文件物化。不同实例各自有 overlay 和 provider，逐主体挂载同一大文件会产生重复副本。

8 MiB 自定义命令输出的实测约 2.03 ms，RSS 从 42.34 MB 升至 66.55 MB，结果仅保留 1 MiB stdout。返回码为 0，同时 `stdout_truncated=True`。因此适配器必须把截断状态独立传给调用者，并保持有界 read 的继续读取能力；仅检查 exit_code 会丢信息。该计时包含默认输出截断，不能称为完整 8 MiB 吞吐量。1 MiB 输出逐字节相等已单独测试。

just-bash 的公开 IFileSystem 提供整文件 readFile、readFileBytes、readFileBuffer，所读接口没有 offset/length 流式读取合同。其内存 lazy 路径同样调用 provider 获取完整内容、编码后写入缓存。更换 shell 实现本身无法消除共享大 World 的整读成本；有界查询应放在数据服务接口。

### 2.3 私有工作区恢复

snapshot/from_snapshot 已恢复 `/workspace/note` 原文；自定义 data 命令通过恢复时重新绑定 callback 继续工作。snapshot 涉及解释器状态和 VFS，已配置 files 的实例恢复时需匹配文件系统后端配置，自定义 builtin 也需重新提供。实验使用官方普通 snapshot 接口，没有加入内容寻址 commit、额外散列或自己的校验机制。

后续实现应将 snapshot 的保存范围限定为该主体主动写入的工作区与确需保留的 shell 状态。共享数据通过回调读取，避免把共享 World 或每次查询结果隐式灌入所有主体快照。若需要跨版本、可审阅的长期工作区格式，可以直接导出私有文件正文与路径；其成本随私有工作区实际体积增长。完整 Thread 继续独立保存，shell snapshot 不承担 Thread 替代语义。

## 三、代价

现有证据支持在 Python 主流程内使用原生绑定，尚未支持把整个仿真迁移到另一语言。

### 3.1 激活与 GIL

同一探针进程连续创建、执行 echo、释放 100 次空 Bash 实例，中位约 32.4 微秒，p95 约 39.3 微秒。RSS 从 32.13 MB 到循环释放后 35.23 MB；另保留 20 个空实例后约 36.16 MB，相对增加约 0.93 MB。该数值是热进程、小工作区的本地微实验，未计跨进程冷启动、大工作区导入及真实数据服务延迟。

官方绑定的 execute_sync 使用 `py.detach` 释放 GIL 后等待 Tokio，async execute 转为 Python awaitable；同一实例由异步互斥锁串行执行。Python lazy provider 与自定义 Python 命令仍回到 Python 运行，Python CPU 密集逻辑不会因此自动获得多核。后续可以按激活创建实例、恢复私有文件、结束后释放，避免每个休眠主体常驻运行时；活跃实例数量仍应由调度配置约束。

### 3.2 语言选择

Python Core 直接调用 Rust 原生绑定已经满足小管道与 async scoped 服务接通，具有现有模型、仿真代码和测试资产可复用的优势。大正文进出跨语言边界会分配与复制，给 Core 设置有界请求和响应比全面换语言更直接。

just-bash 对 TypeScript 应用可在同一 JavaScript 进程工作。当前 Python Core 若使用 Node 子进程，需要进程生命周期、请求响应编码、跨进程取消和回调路由，至少增加一次边界传输；其具体延迟和 RSS 本轮未测，不能用估计值宣称更慢。若后续已有常驻 Node 服务或 Bashkit 的功能兼容性不足，再用同一完整工作负载比较这条路线。

## 四、建议

首版选择 Bashkit 作为可替换的 shell 插件候选，Core 和业务环境保持 Python。接入时以少量 data query/read 命令连接主体信息视图，限制每次传输规模并公开总数、游标、revision 和截断信息；完整内容通过后续读取取得。普通工作区文件继续支持 cat、jq、head、tail 与管道。

产品接入前仍需验证真实 scoped 服务、权限变化与 revision 过期、取消中的异步回调、私有工作区多轮恢复，以及默认输出限制在调用合同中的呈现。当前五项试验与源码核对证明候选路线可行，不承担完整 Core 或业务决策质量验收。本轮未改项目依赖、未安装服务、未读取密钥。
