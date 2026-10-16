# 给主体保存笔记与 Shell 工作区

要让主体把调查笔记、中间值和待核对的问题留到下一次激活，就把工作区和 Shell 接进运行：先装 shell 依赖，再在同一次运行里装配主体数据、interaction、workspace 和模型驱动。共享环境里的资料怎么准备，见[信息入口](information.md)。

## 把工作区接进计划

```bash
uv sync --frozen --extra llm --extra shell
```

把 `workspace_plugin()` 加进计划。该服务依赖同一个数据库里的主体 schema；驱动就算在装配时就要求工作区服务，compose 也会先把所有数据结构建好。

下面是一个完整的驱动插件工厂，供已有计划调用。它借用名为 models 的 research profile、cognition 的 input_builder，以及 threads、interaction、workspace、storage 这几个服务。

```python
from society0.kernel.plugins import Plugin
from society0.kernel.llm import LLMDriver
from society0.kernel.shell import ShellSession


def notebook_driver_plugin():
    def install(context):
        threads = context.require("threads", "threads")
        provider = context.require("models", "models")["research"]
        inputs = context.require("cognition", "input_builder")
        information = context.require("interaction", "information")
        workspace = context.require("workspace", "workspace")
        store = context.require("storage", "store")

        def shell_factory(session, ledger):
            return ShellSession(
                session.scope, information, bound_actions=ledger,
                result_dir=store.path / "shell-results", workspace=workspace,
            )

        def factory(record):
            return LLMDriver(provider, threads, input_builder=inputs,
                             shell_factory=shell_factory)
        context.provide("factory", factory)

    return Plugin("notebook_driver", requires=(
        "threads", "models", "cognition", "interaction", "workspace", "storage",
    ), install=install)
```

在 actor_plugin 里把持久驱动标签映射到 `("notebook_driver", "factory")`。这个工厂展示的是怎么取资源；正式研究可以改用标准的 llm_driver_plugin，保住已有的策略、扩展和模型选择器，再把按同样关系构造的 shell_factory 传进去。

## 在 Shell 里用文件和数据命令

下面这些命令在主体的 Bashkit 工具里执行；终端宿主自己的文件系统和这个逻辑空间互不相干。

```bash
mkdir -p /workspace/notes
printf '下一轮核对资料发布日期。\n' > /workspace/notes/questions.txt
cat /workspace/notes/questions.txt
data list /world
```

`/world` 放的是授权给主体的共享资料，`/context` 保留本次实际收到的认知，`/workspace` 是当前主体的私有持久目录，`/results` 保存已经登记的输出。普通的相对路径默认从工作区解析；临时解释器目录每次新激活都会重建。写完笔记要通知另一位主体，就调用环境里的消息行动。

资料很大时优先用范围读取或 SQL 查询。假设已经装好[报告插件](information.md)，就可以在 Shell 里执行：

```bash
data query /world/reports/items '{"fields":["id","title"],"limit":1}'
data read /world/reports/text/1 '{"offset":0,"size":4096}'
```

按返回的 `next_cursor` 或 `next_offset` 接着读，并且带上版本约束。Bashkit 的某些文件命令会把选中的文件整个读出来；`head` 输出短，不代表底层只读了很少的字节。

## 验证笔记能跨激活保留

让 Alice 在第一步写一条已知笔记，第二步读同一个路径；再从第 1 步的完整步骤恢复到新目录，确认笔记原文一致。Bob 的工作区里应该只有自己的内容。LLMDriver 在 completed 或 waiting 结束时收束变更，完整步骤由 Runtime 发布。

工作区按文件记增量，正文没改就复用已有工件；改一个超大文件，可能要把整个文件复制一遍。命令预览被截断时，沿着 stdout/stderr 返回的原文引用继续读，并分别检查 exit_code、业务行动回执和输出是否完整。动作已经执行、输出却没保存成功时，把这一步留作失败步骤，不要重发一个已经产生事实的动作。

完整参数和生命周期见[工作区参考](../reference/workspace.md)、[Shell 参考](../reference/shell.md)。共享资料每次访问都要重新过授权，私有笔记跟着[完整步骤恢复](storage.md)继续提供。
