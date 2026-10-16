# 给主体保存笔记与 Shell 工作区

主体可以在自己的工作区保存调查笔记、计算中间值与待核对问题。先安装 shell 依赖，并在同一运行中装配主体数据、interaction、workspace 和模型驱动。共享环境资料的准备见[信息入口](information.md)。

## 一、接入持久工作区

```bash
uv sync --frozen --extra llm --extra shell
```

将 `workspace_plugin()` 加入计划。该服务依赖同库的主体 schema；即使驱动需要先取得工作区服务，compose 也会先建好所有数据结构。

以下是完整的驱动插件工厂定义，供已有计划调用。它借用名为 models 的 research profile、cognition 的 input_builder，以及 threads、interaction、workspace、storage 服务。

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

在 actor_plugin 中将持久驱动标签映射为 `("notebook_driver", "factory")`。这个工厂展示资源取得方式；正式研究可以选择标准 llm_driver_plugin，保留已有策略、扩展和模型选择器，再传入按同样关系构造的 shell_factory。

## 二、使用文件与数据命令

以下命令在主体的 Bashkit 工具内执行，终端宿主的文件系统与该逻辑空间各自独立。

```bash
mkdir -p /workspace/notes
printf '下一轮核对资料发布日期。\n' > /workspace/notes/questions.txt
cat /workspace/notes/questions.txt
data list /world
```

`/world` 提供授权的共享资料，`/context` 保留本次实际认知，`/workspace` 是当前主体的私有持久目录，`/results` 保存已登记输出。普通相对路径默认从工作区解析；临时解释器目录在新激活时重新建立。写入笔记后若要通知另一位主体，调用环境中的消息行动。

大型资料优先用范围读取或 SQL 查询。假设已经安装[报告插件](information.md)，可以在 Shell 内执行：

```bash
data query /world/reports/items '{"fields":["id","title"],"limit":1}'
data read /world/reports/text/1 '{"offset":0,"size":4096}'
```

按返回的 next_cursor 或 next_offset 继续，并保留版本约束。Bashkit 的某些文件命令可能整读所选文件；`head` 的短输出不意味着只读取很少字节。

## 三、验证跨激活保存

让 Alice 在第一步写入一条已知笔记，第二步读取同一路径；再从第一步完整点恢复到新目录，检查笔记原文相同。Bob 的工作区应保持自己的内容。LLMDriver 在 completed 或 waiting 结束时收束变更，完整步骤由 Runtime 发布。

工作区以文件为增量单位，未改正文复用既有工件；修改超大文件可能复制整个文件。命令预览被截断时，沿 stdout/stderr 返回的原文引用继续读取，并分别检查 exit_code、业务行动回执与输出完整性。动作已执行而输出保存失败时保留失败步骤，避免重发已产生事实的动作。

完整参数和生命周期见[工作区参考](../reference/workspace.md)、[Shell 参考](../reference/shell.md)。共享资料仍逐次经过授权，私有笔记随[完整步骤恢复](storage.md)继续提供。
