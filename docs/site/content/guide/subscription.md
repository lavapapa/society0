# 使用 ChatGPT 账户授权模型

要用 ChatGPT 账户的额度跑模型主体，Society0 有两条订阅接入路径：已经有 Codex CLI OAuth 的走 `openai-codex`，想用独立应用授权的走 `siwc`。开始前先装好 llm 依赖，确认账户允许用哪些模型、额度有多少；授权凭据保存在运行目录之外。下面几条运行命令会真的消耗账户额度。

## 复用已有的 Codex CLI 登录

先由账户持有人完成自己的 Codex CLI 登录。下面是资源插件的装配片段，需要同一主机上的 threads 服务，`SOCIETY0_CODEX_MODEL` 指定该账户实际可用的模型。

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

驱动绑定 `provider=("models", "models", "codex")`。适配器从标准的 CLI 凭据来源以只读方式取得授权，`CODEX_HOME` 可以指定已经授权的目录；刷新结果留在进程里，登录和凭据的持久管理都交给 CLI。这条路径不用自己填 API key 或 base_url，`session_transport` 保持默认 None。

先用一个短计划确认实际请求能拿到成功终态，再扩大主体和轮次。订阅失败会保留原错误，配置不会自动改走付费 API。下面的仓库示例工厂显式使用 SIWC；选 CLI OAuth 的话，用上面的 profile 接进自己的计划。

## 走 SIWC 应用授权

在[安装](../start/installation.md)得到的源码仓库根目录装好模型依赖，然后由账户持有人在自己的终端发起登录：

```bash
uv sync --frozen --extra llm
uv run python -m society0.kernel.auth login --account default
```

登录命令会打开浏览器，账户持有人选好身份并完成授权。要手动打开链接就加 `--no-browser`；`--port` 指定本地回调端口，`--directory` 指定凭据目录。默认目录是 `~/.config/society0/chatgpt`，不同的 account 名称各存一份凭据。

服务会在发出实际请求之前取用并刷新凭据。要单独刷新，就在同一个授权环境里执行：

```bash
uv run python -m society0.kernel.auth refresh --account default
```

可以公开的配置里只写 `default` 这样的账户名称。分享研究配置和运行工件时，凭据仍然由各自的授权环境管理。资格或额度出问题会留下真实错误，可以按[诊断指南](troubleshooting.md)看它影响到哪一步。

## 跑通 SIWC 示例

仓库里的 `examples/core_next/codex_subscription.py` 已经把主体、认知、行动、Thread、模型和结果连在一起：一位参与者讨论公共议题，每次决定都存成表。下面的命令生成一份两步配置；先把 `ACCOUNT_MODEL_ID` 换成该账户实际可用的模型 ID。

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

源码身份从当前 checkout 读取。`steps` 是正整数，时间线从 1 开始；`concurrency` 和 `timeout` 由示例传给订阅端点。配置里还可以另写 `persona`、`environment` 或 `policy`，用来改主体背景、研究任务和 LLM 策略。运行时换一个新的输出目录：

```bash
uv run python -m society0.kernel.runner \
  --factory examples.core_next.codex_subscription:build \
  --config runs/subscription/config.json \
  --output runs/subscription/run
```

这一步会调用提供方，并消耗账户额度。模型请求拿到完整的成功终态之后，驱动才执行它返回的工具；截断、失败和缺少成功终态的响应都按未完成保留下来供诊断。用观察接口读实际运行状态和 Thread：

```bash
uv run python -m society0.kernel.observation runs/subscription/run
```

结果与完整会话的读取见[结果教程](../tutorial/results.md)和[观察教程](../tutorial/observation.md)。订阅模型能用什么，要按账户实际接入的情况核对，API 模型目录不能代替账户资格的判断。

## 在自己的计划里核对模型与记忆

自己写的运行计划照样用 `model_plugin`。下面这段模型插件片段放进已有 Thread 服务的 `plugins` 列表，`model_id` 从可以公开的配置里取。

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

驱动用 `provider=("models", "models", "codex")`，其余认知输入和动作装配沿用[LLM 教程](../tutorial/llm.md)。订阅适配会拒绝它不支持的请求字段；温度、输出上限这些参数按[模型参考](../reference/models.md)里订阅路径允许的字段选。

要加记忆，embedding 得单独配它的提供方、模型和费用条件。记忆提取也用订阅模型时，给 `memory_plugin` 显式传 `extraction_options={}`，把普通提取器默认的 `max_tokens=4096` 换掉：订阅 profile 不接受这个输出上限字段。自动写入、自动召回和主体主动调用的记忆工具仍然按[记忆教程](../tutorial/memory.md)分别配置。

运行前确认账户名、模型、时间和主体配置都已经写进可以公开的运行配置；运行后按完整步骤、Thread 和领域结果判断实际做到哪一步。授权成功说明账户连接建起来了，研究结论仍然要靠对应的实验设计和事实核对。
