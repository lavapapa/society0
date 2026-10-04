# 工作区与共享文件挂载试验

本文保留选型阶段的试验条件、测量和当时结论。文中的“当前”“候选”“尚未实现”均指该次试验时点；现行接口与装配方式见[工作区合同](workspace-contract.md)、[Shell 合同](shell-contract.md)与[安装说明](installation.md)。阶段状态与后续验收由 [TODO](TODO.md) 记录。

本次核实 Bashkit 0.18.2 已安装 Python 接口，分别测量未修改私有工作区的快照成本，以及真实只读目录和懒文件的读取行为。现有数据命令保持完整查询能力；普通 `/world/...` 文件路径需要额外明确的挂载适配。

## 一、实际成本

`benchmarks/core_next_workspace_probe.py` 使用独立临时目录，结束自动清理大文件；结果保留在 `research/core-next/workspace-native-probe.json`。同一份固定种子高熵二进制文件在十次激活间保持原样，每次快照后新建解释器并逐字节验证原文件、变量和目录状态。

1 MiB 工作区每次快照约 1.054 MB，十次合计 10.54 MB，中位快照耗时 3.44 ms；8 MiB 工作区每次约 8.423 MB，十次合计 84.23 MB，中位 28.94 ms。现有原生快照会压缩全文，重复字符对照十次仅约 18 KB，仍需读取和编码原始工作区；低熵结果另存 `workspace-native-low-entropy-probe.json`。这些是热进程快照开销，尚未计 ActorStore 工件封存、持久化和多主体调度。

`exclude_filesystem=True` 得到约 1.25 KB 的 shell 状态并保留变量和 cwd。它需要另有正确持久化的文件系统，单独采用会遗漏私有文件。将共享可变目录直接挂载也无法保留旧完整步骤的数据身份。

## 二、挂载行为

`FileSystem.real(path)` 加 `bash.mount('/world/docs', fs, read_only=True)` 已实测支持 ls、cat，写入被原生文件系统拒绝。shell-only 快照恢复后重新挂载，原文、变量和 cwd 均正确。调用方须给出已授权、稳定的文件集合；该试验未将任意 SQL 表自动变成文件。

Python `files={path: callback}` 可以列出尚未加载的文件。对 8 MiB 文件执行 `head -c 16` 时，首次调用加载器取得全部 8 MiB，第二次读取使用缓存。注册目录不会提前加载正文；注册每个对象的路径仍需要枚举对象。现有 API 提供的是同步整文件加载器，尚未提供 Python 侧动态目录与异步范围文件系统桥。当前安装接口的 ExecResult 没有文件改动清单或脏版本字段。

大型数据集继续适合 `data query/read`：查询下推、原文范围与游标已经具有实际提供者支持。小文档可用成熟文件挂载成为普通 cat/jq 消费对象。二者具有不同的首次物化成本，挂载入口要公开该边界。

## 三、工程选择

Bashkit 的 `FileSystem.from_capsule` 接受原生 `bashkit.FileSystem.v1` ABI，可连接现成原生文件系统，但 Python SDK 没有直接提供任意 async Information 到该 ABI 的适配。其原生内容寻址 commit 路线包含散列身份，本项目未采用。

[AgentFS 官方说明](https://github.com/tursodatabase/agentfs)提供 SQLite 文件系统、Python/Rust SDK 与系统挂载。其 [Python SDK](https://github.com/tursodatabase/agentfs/tree/main/sdk/python)公开异步文件操作；本轮未找到可直接接 Bashkit capsule 的 Python 适配证据。系统挂载或新增原生桥会引入部署、资源生命周期和恢复协议工作，因此当前尚未替换正式工作区后端。官方项目仍标为 beta，本轮未安装或运行它。

后续实现先把可见信息挂载和私有文件增量恢复分别验收。前者需要真实 scoped 信息消费者、目录分页与身份边界；后者需要未改工作区复用、写入后的旧步骤恢复、休眠主体不持有解释器，以及故障时完整工作区身份。现有全文快照具备正确性证据，持续成本仍随累计私有文件体积增长，后续取舍必须包含这项测量。

## 四、原生桥试验

`benchmarks/native_fs_probe` 是约一百行 Rust 胶水，固定 Bashkit 0.18.2，采用官方 `FileSystem.v1` capsule ABI。文件操作交给原生 OverlayFs；Python async 回调提供动态只读下层，并通过 PyO3 调度回原调用者事件循环。原生 OverlayFs 的 snapshot 包含 upper，删除标记未包含在其中，因此桥额外记录成功 remove/rename 的源路径。当前这项接口仍属试验。

`benchmarks/core_next_native_fs_probe.py` 用一个 SQLite current 文件索引作为下层，每次激活创建新的 overlay，提交 upper 与删除清单后丢弃 overlay。三次激活依次读取未改文件、写入并重命名小文件、删除小文件，导出正文分别为 0、5、0 字节；shell-only 状态约 1.25 KB。索引查询定位当前路径，读取深度不随激活次数增加。SQLite backup 保留的旧完整点恢复后原文件逐字节一致。动态 `/world` 目录能立即发现新增文件，关闭 scope 后等待中的读操作拒绝返回正文；作用域 owner 取消并收束 Python 回调，所有权有明确归属。

结果在 `research/core-next/native-fs-probe.json`。8 MiB 文件的 `head -c 16` 仍读全部 8 MiB，首次读取进程 RSS 高水位增加约 20.5 MB；对它 append 一个字节，导出的改动正文为 8 MiB 加一字节。前者来自整文件 `FileSystem.read_file` 合同，后者来自原生 OverlayFs 文件级 copy-up。该数据没有证明任意文件操作具备块级内存上界，也未覆盖大目录枚举。原生 upper 默认包含少量系统目录和空设备文件，试验保留这些条目，不按名称过滤掉用户后来写入的文件。

macOS arm64 本机已经编译并运行此桥，构建使用 Rust 1.95.0、PyO3 0.29 abi3-py39；所有二进制和依赖构建缓存位于 `/tmp`，仓库保留小源码与锁文件。复跑如下：

```sh
cargo +1.95.0 build --manifest-path benchmarks/native_fs_probe/Cargo.toml --target-dir /tmp/society0-native-fs-target
cp /tmp/society0-native-fs-target/debug/libsociety0_fs_probe.dylib /tmp/society0_fs_probe.abi3.so
PYTHONPATH=/tmp .venv/bin/python benchmarks/core_next_native_fs_probe.py
```

产品接入还需独立审查、真实 StageStore/Actor/LLM 消费者、当前文件索引与 complete 工件闭环，以及 shell 可选 wheel 的安装验证。该桥使进一步接入具备实际依据，正式工作区后端目前仍保持原实现。
