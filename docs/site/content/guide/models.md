# 配置模型与嵌入服务

要让主体用模型作决定，或者给记忆和语义推荐配嵌入，先把模型、端点、实际支持的请求参数和调用容量定下来，再把这些连接作为共享服务交给驱动。下面以 OpenAI 兼容端点为例；Anthropic、Google 和订阅入口各有自己的适配与依赖。主体决策循环见[LLM 教程](../tutorial/llm.md)。

## 准备凭据和插件定义

先装上模型依赖；换用别的提供方时，选对应的 anthropic 或 google 可选组。

```bash
uv sync --frozen --extra llm
```

在运行环境里提供 `SOCIETY0_LLM_API_KEY`，并把实际的模型 ID 和地址设好。下面这个资源插件定义可以导入，保存为 `study_models.py`；它需要同一主机上的 threads 服务，本身还不是一份完整的运行计划。

```python title="study_models.py"
import os
from society0.kernel.models import model_plugin


def study_models():
    return model_plugin({
        "research": {
            "endpoints": [{
                "id": "research-endpoint",
                "provider_type": "openai",
                "base_url": os.environ["SOCIETY0_LLM_BASE_URL"],
                "api_key": os.environ["SOCIETY0_LLM_API_KEY"],
                "model": os.environ["SOCIETY0_LLM_MODEL"],
                "concurrency": int(os.environ["SOCIETY0_LLM_CONCURRENCY"]),
                "timeout": 120,
                "trust_env": True,
            }],
            "max_attempts": 1,
        },
    })
```

把这个插件加进计划后，驱动用 `provider=("models", "models", "research")` 取具名 profile。端点的 concurrency 要按已知的服务容量来设；这里的 timeout 和尝试次数只是例子里的取值。`trust_env=True` 让 HTTP 客户端沿用环境的网络配置，直连环境可以显式关掉。

## 选 profile 和请求参数

同一个 profile 下的多个端点服务同一个模型，可以分别配权重、容量和总许可。不同模型放在不同 profile 里，由不同的驱动工厂绑定，或者交给 provider_selector 按明确的研究条件选。实际选了哪个要记进 RunContract，用来解释主体之间的差异。

`request_options` 给出默认的请求参数，每次调用时同名的顶层选项会覆盖默认值，嵌套对象则是整体替换。工具阶段和访谈阶段可能要不同的提供方参数；先确认端点确实接受工具、结构化返回和推理设置，再把配置定下来。普通 API 密钥、Codex CLI OAuth 和 SIWC 用三种不同的凭据来源，见[订阅指南](subscription.md)。

## 单独配嵌入服务

记忆和语义推荐都要嵌入服务。下面这段装配片段加进已有计划；环境变量对应单独授权的嵌入端点。

```python
import os
from society0.kernel.models import embedding_plugin

embeddings = embedding_plugin({
    "semantic": {
        "endpoints": [{
            "id": "semantic-endpoint",
            "provider_type": "openai",
            "base_url": os.environ["SOCIETY0_EMBED_BASE_URL"],
            "api_key": os.environ["SOCIETY0_EMBED_API_KEY"],
            "model": os.environ["SOCIETY0_EMBED_MODEL"],
            "concurrency": int(os.environ["SOCIETY0_EMBED_CONCURRENCY"]),
            "send_dimensions": False,
        }],
        "dimensions": int(os.environ["SOCIETY0_EMBED_DIMENSIONS"]),
        "max_attempts": 1,
    },
})
```

能用通用具名服务引用的消费者（比如 memory_plugin）写 `("embeddings", "embeddings", "semantic")`；social_plugin 的 embedding 参数只用 `(插件名, profile名)` 两段，见[社交接线](social.md)。`dimensions` 用来核对返回向量的长度；`send_dimensions=False` 适合由模型决定固定维度的端点，返回的维数必须和配置一致。嵌入批次保留每段文本与向量的位置关系，合批不会把不同主体的经历并到一起。纯规则运行要用嵌入时，可以明确设 `threads=None`，调用归属仍然由消费者给出。

## 先做一次短验证

先初始化自己的计划，核对插件依赖和 profile 名称，然后在授权预算内跑一次工具决定和一组已知文本的嵌入。看 Thread 里的完整请求、响应和终态，核对向量数量、维度以及各条属于哪位主体。订阅模型本身不提供嵌入能力，记忆要继续用独立的嵌入服务。

用 `Observation.resource_usage()` 查看实际的物理尝试、错误和提供方报告的用量。提供方没报告的 token 或缓存数就当未知，跨主体共享的嵌入批次，归属值不能直接相加；全局用量要从全局统计里读。调网络容量见[三类并发](parallel.md)，失败处理见[排查指南](troubleshooting.md)。精确参数、重试和计量方式集中在[模型参考](../reference/models.md)。
