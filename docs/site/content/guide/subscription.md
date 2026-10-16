# 使用 ChatGPT 账户授权模型

Society0 提供两条明确的订阅接入路径：已有 Codex CLI OAuth 使用 `openai-codex`，独立应用授权使用 `siwc`。先安装 llm 依赖并确认账户允许的模型及额度，授权凭据保存在运行目录之外。以下运行命令会使用真实账户额度。

## 一、复用 CLI 授权

由账户持有人先完成自己的 Codex CLI 登录。下面是资源插件接线片段，需要同一主机的 threads 服务，`SOCIETY0_CODEX_MODEL` 指定该账户实际可用的模型。

```python
import os
from society0.kernel.models import model_plugin

models = model_plugin({"codex": {
    "endpoints": [{
        "id": "codex-oauth", "provider_type": "openai-codex",
        "model": os.environ["SOCIETY0_CODEX_MODEL"],
        "concurrency": 1, "timeout": 120, "trust_env": True,
    }],
    "max_attempts": 1,
}})
```

驱动绑定 `provider=("models", "models", "codex")`。适配器从标准 CLI 凭据来源只读取得授权，`CODEX_HOME` 可指定已授权目录；刷新结果留在进程内，CLI 负责登录及凭据持久管理。这一路径无需自行填写 API key 或 base_url，`session_transport` 保持默认 None。

先用自己的短计划检查实际请求成功终态，再扩大主体与轮次。订阅失败保留错误，配置不会自动切到付费 API。下面的仓库示例工厂显式使用 SIWC；选择 CLI OAuth 时，使用上面的 profile 接入自己的计划。

## 二、完成 SIWC 授权

在[安装](../start/installation.md)得到的源码仓库根目录安装模型依赖，再由账户持有人在自己的终端发起登录：

```bash
uv sync --frozen --extra llm
uv run python -m society0.kernel.auth login --account default
```

登录命令打开浏览器，账户持有人选择身份并完成授权。需要手动打开链接时增加 `--no-browser`；`--port` 设置本地回调端口，`--directory` 指定凭据目录。默认目录是 `~/.config/society0/chatgpt`，不同 account 名称分别保存凭据。

服务会在实际请求前取得并刷新凭据。需要单独刷新时，在同一授权环境执行：

```bash
uv run python -m society0.kernel.auth refresh --account default
```

公开配置保存 `default` 这样的账户名称。分享研究配置与运行工件时，凭据继续由各自的授权环境管理。资格或额度失败会留下实际错误，可通过[诊断指南](troubleshooting.md)查看其影响范围。

## 三、运行 SIWC 示例

仓库中的 `examples/core_next/codex_subscription.py` 已连接主体、认知、行动、Thread、模型与结果。它让一位参与者讨论公共议题，并把每次决定保存成表。以下命令生成两步配置；先把 `ACCOUNT_MODEL_ID` 替换为该账户实际可用的模型 ID。

```bash
mkdir -p runs/subscription
uv run python - <<'PYCODE'
import json
import subprocess
from pathlib import Path

config = {
    "model": "ACCOUNT_MODEL_ID",
    "account": "default",
    "release": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True).strip(),
    "steps": 2,
    "concurrency": 1,
    "timeout": 120,
}
Path("runs/subscription/config.json").write_text(
    json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
PYCODE
```

源码身份从当前 checkout 读取。`steps` 是正整数，时间线从 1 开始；`concurrency` 与 `timeout` 由示例传入订阅端点。可在配置中另行写入 `persona`、`environment` 或 `policy`，改变主体背景、研究任务与 LLM 策略。运行时选择新的输出目录：

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.codex_subscription:build \
  --config runs/subscription/config.json \
  --output runs/subscription/run
```

这一步会调用提供方并使用账户额度。模型请求得到完整成功终态后，驱动才执行返回的工具；截断、失败和缺少成功终态的响应按未完成诊断保留。通过观察接口读取实际运行状态与 Thread：

```bash
uv run python -m society0.kernel.observation runs/subscription/run
```

完整结果与会话读取见[结果教程](../tutorial/results.md)和[观察教程](../tutorial/observation.md)。订阅模型的能力要按账户实际接入条件核对，API 模型目录不能代替账户资格判断。

## 四、核对计划与记忆

自定义运行计划仍使用 `model_plugin`。下面是模型插件片段，放在已有 Thread 服务的 `plugins` 列表中；`model_id` 由公开配置取得。

```python
from society0.kernel.models import model_plugin

models = model_plugin({
    "codex": {
        "endpoints": [{
            "id": "chatgpt",
            "provider_type": "siwc",
            "model": model_id,
            "account": "default",
            "concurrency": 1,
            "timeout": 120,
        }],
        "max_attempts": 2,
    },
})
```

驱动使用 `provider=("models", "models", "codex")`，其余认知输入与动作装配沿用[LLM 教程](../tutorial/llm.md)。订阅适配会拒绝不支持的请求字段；温度、输出上限等配置应按[模型参考](../reference/models.md)中的订阅合同选择。

若加入记忆，embedding 需要独立配置其提供方、模型与费用条件。记忆提取也使用订阅模型时，向 `memory_plugin` 显式传入 `extraction_options={}`，替换普通提取器默认的 `max_tokens=4096`：订阅 profile 不接受这一输出上限字段。自动写入、自动召回与主体主动记忆工具仍按[记忆教程](../tutorial/memory.md)分别配置。

运行前确认账户名、模型、时间与主体配置已进入公开合同，运行后依据完整步骤、Thread 和领域结果判断实际完成范围。授权成功表示账户连接建立，研究结论仍需要对应的实验设计与事实核对。
