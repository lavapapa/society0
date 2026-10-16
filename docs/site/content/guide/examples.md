# 选择一个完整示例

想跟着一份能跑的代码把插件装配、主体、调度和结果连起来，就从公开示例里挑一个和研究问题最接近的，再按教程替换其中一个机制。下面出现的路径都相对于 Society0 仓库根目录。

## 规则运行与两个机制的对话

`examples/core_next/rule_run.py` 是[快速开始](../start/quickstart.md)用的基础规则计划。`examples/core_next/conversation_pilot.py` 让四位主体同时参加两个具名交流机制，每个时点先配对再发消息，结果里保存的是实际配对和消息。

沿用快速开始那份两步配置，就能跑对话示例：

```bash
mkdir -p runs/conversation
uv run python -m society0.kernel.runner \
  --factory examples.core_next.conversation_pilot:build \
  --config runs/hello/config.json \
  --output runs/conversation/run
```

这个示例用规则 Driver，主体 `a、b、c、d` 在 `work` 和 `commons` 两个机制里交流。每个对话机制实例各自维护自己的事实，同一批主体共用一个环境。跑完之后可以按[结果教程](../tutorial/results.md)导出工作台 JSON。

## 带模型和记忆的两轮实验

`skill/assets/minimal_experiment.py` 是一份完整的两轮模型实验：Alice 读一条消息，执行查看详情的行动，写入经验，再回答一次结构化的可信度测量。它同时需要 LLM、embedding 和记忆依赖。

```bash
uv sync --frozen --extra llm --extra memory
```

在同一个终端里设置可以公开的模型信息和私有密钥。下面的占位值要换成你所用服务实际支持的模型和向量维数；密钥通过终端或你自己的环境管理器提供。

```bash
export SOCIETY0_RELEASE="$(git rev-parse HEAD)"
export SOCIETY0_LLM_BASE_URL="https://YOUR_PROVIDER/v1"
export SOCIETY0_LLM_MODEL="YOUR_CHAT_MODEL"
export SOCIETY0_EMBED_BASE_URL="https://YOUR_PROVIDER/v1"
export SOCIETY0_EMBED_MODEL="YOUR_EMBEDDING_MODEL"
export SOCIETY0_EMBED_DIMENSIONS="YOUR_VECTOR_DIMENSIONS"
# 在当前环境设置 SOCIETY0_LLM_API_KEY 与 SOCIETY0_EMBED_API_KEY。
```

先跑一次只做初始化的检查，再换一个新目录正式跑两轮：

```bash
uv run python skill/assets/minimal_experiment.py \
  --check --run-dir runs/llm-study/check
uv run python skill/assets/minimal_experiment.py \
  --run-dir runs/llm-study/run
```

`--check` 用空时间序列，不会发出模型请求。正式运行会调用提供方服务，按所选模型产生费用。特定于模型的选项通过 `SOCIETY0_LLM_OPTIONS` 的 JSON 配置；工具阶段参数和其他调用阶段怎么区分，见[模型教程](../tutorial/llm.md)。

## 跑一遍公共组合场景

想弄清多来源认知或服务替换，可以跑一遍确定性的组合场景。它用完整的 LLMDriver 和脚本化提供方，走真实的组合流程，但不请求外部模型。先装好 llm、social 依赖，并为这次尝试新建配置和输出目录：

```bash
uv sync --frozen --extra llm --extra social
mkdir -p runs/composition
uv run python - <<'PY'
import json
import subprocess
from pathlib import Path
config = {
    "release": {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()},
    "case": "multi-source", "variant": "default", "provider": "scripted",
    "start": 1, "end": 2,
}
Path("runs/composition/config.json").write_text(json.dumps(config), encoding="utf-8")
PY
uv run python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config runs/composition/config.json --output runs/composition/run
uv run python -m society0.kernel.observation runs/composition/run
```

确认两个步骤都已完成，再读 Thread 里的社交与通知原文、各自的来源位置，以及保存之后的确认。接着按[公共组合说明](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/README.md)还能做独立进程恢复和只读事实比对。

| 想检查的边界 | case / variant |
| --- | --- |
| 来源次序与空位置 | multi-source / reordered、empty-values |
| 同主体重新进入原 Moment | multi-source / moment-switch |
| 外部主体目录 | configuration / external-directory |
| 推荐来源与分页变化 | recommendation / external-selection、paging |
| 登记撤回与所有权 | registration / re-register、owned |
| Runtime 与 worker 退出 | shutdown / runtime-first、worker-first |
| Runtime/Schedule 替换 | extensions / runtime-replacement |

这几个场景各有配置要求和预期的成功或失败边界，选定后按说明构造新目录，不要只换 case 名就沿用原来的配置。

## 按数据形态挑示例

| 示例文件 | 适合学习的内容 |
| --- | --- |
| `examples/core_next/typed_records.py` | 追加事实、当前投影、复合事务与精确值 |
| `examples/core_next/immutable_catalog.py` | 不可变大正文、热引用与数据集 |
| `examples/core_next/graph_environment.py` | 外部准备、持久图数据与派生视图 |
| `examples/core_next/parallel_graph.py` | 共享计算服务与多进程计算边界 |
| `examples/core_next/shared_environment.py` | SQL 信息、行动、shell 和多个机制的共享状态 |
| `examples/core_next/codex_subscription.py` | 订阅模型与公开运行工厂 |

这些文件各有自己的入口和输入条件。翻一下[当前版本源码](https://github.com/lavapapa/society0/tree/@COMMIT@/examples/core_next)，按文件里定义的工厂或命令行来用。订阅路径另有[完整使用指南](subscription.md)。

## 把一个例子改成自己的研究

先为自己的问题定下初始资料、主体集合、业务时间和测量。改动某个机制时，先在少量主体和短时间范围里核对真实事实与结果，再扩规模。把完整配置和源码身份保存下来，之后就能从共同的完整步骤比较不同的后续条件。

跑通一个完整示例之后，接着[编写插件](plugins.md)或[安排时间与阶段](schedule.md)，把你需要的社会机制一步步写出来。
