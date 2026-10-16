# 选择一个完整示例

完整示例把插件装配、主体、调度和结果连接在一起。建议先选择与你研究问题接近的例子，再按教程替换其中一个机制。以下路径都相对于 Society0 仓库根目录。

## 一、规则与机制组合

`examples/core_next/rule_run.py` 是基础规则计划，供[快速开始](../start/quickstart.md)使用。`examples/core_next/conversation_pilot.py` 让四位主体同时参与两个具名交流机制，每个时点先配对、再发送消息，结果保存实际配对与消息。

沿用快速开始的两步配置运行对话示例：

```bash
mkdir -p runs/conversation
uv run python -m society0.kernel.runner \
  --factory examples.core_next.conversation_pilot:build \
  --config runs/hello/config.json \
  --output runs/conversation/run
```

该示例使用规则 Driver，主体 `a、b、c、d` 在 `work` 与 `commons` 两个机制中交流。对话机制的实例各自维护事实，同一批主体共享整个环境。运行以后，可以按[结果教程](../tutorial/results.md)导出工作台 JSON。

## 二、模型与记忆

`skill/assets/minimal_experiment.py` 是完整的两轮模型示例：Alice 阅读一条消息、执行查看详情的行动、写入经验，再回答结构化可信度测量。它同时需要 LLM、embedding 和记忆依赖。

```bash
uv sync --frozen --extra llm --extra memory
```

在同一终端设置公开的模型信息与私有密钥。下面的占位值需要替换为你使用的服务实际支持的模型及向量维数；密钥通过终端或自己的环境管理器提供。

```bash
export SOCIETY0_RELEASE="$(git rev-parse HEAD)"
export SOCIETY0_LLM_BASE_URL="https://YOUR_PROVIDER/v1"
export SOCIETY0_LLM_MODEL="YOUR_CHAT_MODEL"
export SOCIETY0_EMBED_BASE_URL="https://YOUR_PROVIDER/v1"
export SOCIETY0_EMBED_MODEL="YOUR_EMBEDDING_MODEL"
export SOCIETY0_EMBED_DIMENSIONS="YOUR_VECTOR_DIMENSIONS"
# 在当前环境设置 SOCIETY0_LLM_API_KEY 与 SOCIETY0_EMBED_API_KEY。
```

先执行只初始化的检查，再用另一个新目录运行两轮：

```bash
uv run python skill/assets/minimal_experiment.py \
  --check --run-dir runs/llm-study/check
uv run python skill/assets/minimal_experiment.py \
  --run-dir runs/llm-study/run
```

`--check` 使用空时间序列，不发出模型请求。正式运行会使用提供方服务，按所选模型产生调用成本。模型特定选项可通过 `SOCIETY0_LLM_OPTIONS` 的 JSON 配置；如何区分工具阶段参数与其他调用阶段，见[模型教程](../tutorial/llm.md)。

## 三、检查公共组合

需要理解多来源认知或服务替换时，可以运行确定性组合场景。它使用完整 LLMDriver 与脚本化提供方，调用真实组合流程而不请求外部模型。先安装 llm、social 依赖，并为这次尝试创建新的配置和输出目录：

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

核对两个步骤均已完成，再读取 Thread 中的社交与通知原文、各自位置和保存后的确认。按[公共组合说明](https://github.com/lavapapa/society0/blob/@COMMIT@/examples/core_next/README.md)可以继续做独立进程恢复及只读事实比对。

| 想检查的边界 | case / variant |
| --- | --- |
| 来源次序与空位置 | multi-source / reordered、empty-values |
| 同主体重新进入原 Moment | multi-source / moment-switch |
| 外部主体目录 | configuration / external-directory |
| 推荐来源与分页变化 | recommendation / external-selection、paging |
| 登记撤回与所有权 | registration / re-register、owned |
| Runtime 与 worker 退出 | shutdown / runtime-first、worker-first |
| Runtime/Schedule 替换 | extensions / runtime-replacement |

这些场景各有配置要求及预期成功或失败边界，选定一个后按说明构造新目录，避免仅替换 case 名却沿用不适合的配置。

## 四、按数据形态选择

| 示例文件 | 适合学习的内容 |
| --- | --- |
| `examples/core_next/typed_records.py` | 追加事实、当前投影、复合事务与精确值 |
| `examples/core_next/immutable_catalog.py` | 不可变大正文、热引用与数据集 |
| `examples/core_next/graph_environment.py` | 外部准备、持久图数据与派生视图 |
| `examples/core_next/parallel_graph.py` | 共享计算服务与多进程计算边界 |
| `examples/core_next/shared_environment.py` | SQL 信息、行动、shell 和多个机制的共享状态 |
| `examples/core_next/codex_subscription.py` | 订阅模型与公开运行工厂 |

这些文件有各自的入口与输入条件。查看[当前版本源码](https://github.com/lavapapa/society0/tree/@COMMIT@/examples/core_next)，按文件定义的工厂或命令行使用。订阅路径另有[完整使用指南](subscription.md)。

## 五、把例子变成研究

为自己的问题明确初始资料、主体集合、业务时间与测量。改变某个机制时，先在少量主体和短时间范围内核对真实事实与结果，再扩展规模。保存完整配置和源码身份，之后可以从共同完整点比较不同的后续条件。

已经跑通一个完整示例后，继续[编写插件](plugins.md)或[安排时间与阶段](schedule.md)，逐步表达你需要的社会机制。
