# 配置模型与嵌入服务

先为一次研究明确模型、端点、实际支持的请求参数和调用容量，再把连接资源作为共享服务提供给驱动。本页展示 OpenAI 兼容端点；Anthropic、Google 和订阅入口使用各自的适配与依赖。主体决策循环见[LLM 教程](../tutorial/llm.md)。

## 一、准备公开配置与凭据

安装模型依赖；使用其他提供方时选择对应的 anthropic 或 google 可选组。

```bash
uv sync --frozen --extra llm
```

在运行环境提供 `SOCIETY0_LLM_API_KEY`，并设置实际模型 ID 与地址。下面是可导入的资源插件定义，保存为 `study_models.py`。它需要同一主机的 threads 服务，尚不构成完整运行计划。

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

加入该插件后，驱动用 `provider=("models", "models", "research")` 取得具名 profile。端点 concurrency 必须按已知服务容量设定；这里的 timeout 与尝试次数是示例选择。`trust_env=True` 允许 HTTP 客户端使用环境网络配置，直连环境可以明确关闭。

## 二、选择 profile 与请求参数

同一 profile 的多个端点服务同一模型，可配置权重、独立容量和总许可。不同模型使用不同 profile，由不同驱动工厂绑定，或通过 provider_selector 按明确研究条件选择。将有效选择记录到 RunContract，以便解释主体间差异。

`request_options` 提供默认请求参数，每次调用的同名顶层选项覆盖默认值，嵌套对象整体替换。工具阶段与访谈阶段可能需要不同的提供方参数；先确认端点实际接受工具、结构化返回和推理设置，再固定配置。普通 API 密钥、Codex CLI OAuth 与 SIWC 使用不同凭据来源，见[订阅指南](subscription.md)。

## 三、独立配置嵌入

记忆和语义推荐需要嵌入服务。下面是加入已有计划的接线片段；环境变量对应单独获授权的嵌入端点。

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

支持通用具名服务引用的消费者（如 memory_plugin）使用 `("embeddings", "embeddings", "semantic")`；social_plugin 的 embedding 参数则使用 `(插件名, profile名)` 两段形式，见[社交接线](social.md)。`dimensions` 核验返回向量长度；`send_dimensions=False` 适合由模型决定固定维度的端点，返回维数必须与配置相符。嵌入批次保留每段文本和向量的位置关系，合批不会合并主体经历。纯规则使用嵌入时可明确设 `threads=None`，调用归属仍需由消费者提供。

## 四、做短验证再扩展

先初始化自己的计划，核对插件依赖和 profile 名称；随后在授权预算内运行一次工具决定和一组已知文本嵌入。检查 Thread 中的完整请求、响应和终态，核对向量数量、维度及主体归属。订阅模型自身不提供嵌入能力，记忆继续使用独立服务。

通过 `Observation.resource_usage()` 查看实际物理尝试、错误和提供方报告的用量。未报告的 token 或缓存数保持未知，跨主体共享嵌入批次的归属值不能直接相加；全局用量从全局统计读取。网络容量调整参见[三类并发](parallel.md)，失败处理参见[排查指南](troubleshooting.md)。精确参数、重试和计量语义集中在[模型参考](../reference/models.md)。
