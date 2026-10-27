# Next 可运行示例

从规则、对话或 Social 场景开始，再按需要替换插件。示例保存完整步骤、结果和 Thread，支持从完整点恢复；输出由本次运行生成。安装步骤见[安装指南](../../docs/site/content/start/installation.md)，首次结果读取见[运行与恢复](../../docs/core-next/getting-started.md)。

## 一、规则与对话

在仓库根目录完成 `uv sync --frozen --all-extras` 后，为本次尝试建立新目录并保存配置：

```sh
export OUT="$(mktemp -d /tmp/society0-examples.XXXXXX)"
uv run python - <<'PY'
import json, os, subprocess
from pathlib import Path
config = {'release': {'commit': subprocess.check_output(
    ['git', 'rev-parse', 'HEAD'], text=True).strip()}, 'start': 1, 'end': 2}
(Path(os.environ['OUT']) / 'config.json').write_text(json.dumps(config))
PY
uv run python -m society0.kernel.runner \
  --factory examples.core_next.rule_run:build \
  --config "$OUT/config.json" --output "$OUT/rule"
uv run python -m society0.kernel.runner \
  --factory examples.core_next.conversation_pilot:build \
  --config "$OUT/config.json" --output "$OUT/conversation"
uv run python -m society0.kernel.observation "$OUT/conversation"
```

[规则计划](rule_run.py)让 Alice、Bob 各作一次决定；[对话计划](conversation_pilot.py)让四名主体同时进入两个具名轮次机制。每步先配对再发送消息，结果包含配对与完整消息。以上入口均无需模型账号。

## 二、Social 与认知

[组合工厂](composed_cognition.py)接受 `case`、`variant` 和 `provider="scripted"`。以下场景使用完整 LLMDriver、本地固定响应和真实 Social 插件；语义嵌入关闭：

```sh
uv run python - <<'PY'
import json, os
from pathlib import Path
path = Path(os.environ['OUT'])
config = json.loads((path / 'config.json').read_text())
config.update(case='multi-source', variant='default', provider='scripted')
(path / 'social.json').write_text(json.dumps(config))
PY
uv run python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config "$OUT/social.json" --output "$OUT/social"
uv run python -m society0.kernel.runner \
  --factory examples.core_next.composed_cognition:build \
  --config "$OUT/social.json" --output "$OUT/social-restored" \
  --source "$OUT/social" --step 1
uv run python -m society0.kernel.observation "$OUT/social-restored"
```

`multi-source` 将社交内容与持久通知按顺序送进主体认知。两个来源分别保存位置，完整原文与固定响应留在 Thread，通知在输入保存后确认。恢复配置保留完整时间序列 `start=1,end=2`，runner 跳过已完成部分。

需要单独观察排序时，在配置中使用 `case="recommendation", variant="default"`，输出到新目录。[推荐计划](composed_recommendation.py)使用规则 Driver、本地确定性嵌入和 Chroma，保存候选集、完整排名与分页记录。其 `validate(path)` 返回逐项只读检查；`compare(continuous, restored)` 比较连续与恢复后的业务事实。[Social 指南](../../docs/site/content/guide/social.md)说明各叶插件与曝光语义。

## 三、扩展场景

下面的场景仍使用组合工厂及 `provider="scripted"`；每次变更使用新配置和新输出目录。完整参数校验由各文件的 `build` 函数定义。

| case | 示例 variant | 文件与用途 |
|---|---|---|
| `multi-source` | `default`、`reordered`、`discarded`、`interview` | [多来源认知](composed_cognition.py)：顺序、来源选择与访谈 |
| `source-lifecycle` | `default` | [来源生命周期](composed_cognition.py)：省去来源后保留历史位置 |
| `recommendation` | `default`、`external-selection`、`external-relations`、`native`、`semantic-change`、`paging` | [推荐](composed_recommendation.py)：外部来源、变化与分页 |
| `actor-directory` / `artifact-read` | `default` | [目录与正文](composed_directory_artifacts.py)：目录委托与范围读取 |
| `registration` / `manifest` | `default` | [登记与清单](composed_registration_manifest.py)：所有权与组合图 |
| `extensions` | `default`、`memory-write`、`memory-recall`、`memory-tools`、`memory-all` | [激活扩展](composed_shutdown_extensions.py)：独立记忆开关 |
| `configuration` | `legal-two-graphs`、`external-directory`、`base-only` | [配置](composed_configuration.py)：依赖图与服务替换 |

[来源边界](composed_source_edges.py)和[退出扩展](composed_shutdown_extensions.py)还提供受控失败场景；它们按配置产生错误，适合检查自己的恢复与清理流程。

通用机制示例还包括[持久图](graph_environment.py)、[并行图计算](parallel_graph.py)、[类型化记录](typed_records.py)、[不可变数据集](immutable_catalog.py)和[独立推荐观察器](recommendation_observer.py)。[订阅示例](codex_subscription.py)需要明确的外部账号授权，其配置见[订阅指南](../../docs/site/content/guide/subscription.md)。

运行之后通过[Results API](../../docs/site/content/reference/results.md)取数，用[分页指南](../../docs/core-next/results-analysis.md)取得全部大正文，并保留代码、依赖、配置与完整点以供复现。
