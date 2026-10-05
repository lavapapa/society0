# Codex 订阅接入

Society0 通过 Sign in with ChatGPT 授权使用账户允许的计划额度，以 `provider_type='siwc'` 安装模型服务。普通 API 与订阅使用独立配置。外部调用等待完整 Responses 结果，底层消费端点要求的流并确认成功终态后，驱动才执行工具。账户资格、模型可用性和用量以提供方实际返回为准。

## 一、授权

安装 `llm` 依赖后，在自己的终端完成授权：

```sh
uv sync --extra llm
uv run python -m society0.kernel.auth login --account default
```

登录入口打开浏览器，由账户持有人选择身份并同意计划接入。`--no-browser` 打印授权地址，供手动打开；`--port` 设置本地回调端口，`--directory` 指定独立凭据目录。默认目录为 `~/.config/society0/chatgpt`。每个 account 名称保存独立凭据，文件留在仿真目录之外。

服务在实际请求前取得和刷新凭据。需要单独刷新时使用：

```sh
uv run python -m society0.kernel.auth refresh --account default
```

授权流程、账户元数据与刷新由 [auth.py](../../src/society0/kernel/auth.py) 的公开入口管理。运行配置保存 account 名称，不保存 token；共享账户的凭据刷新通过认证设施串行协调。额度或资格错误返回明确失败，没有收费端点回退。

## 二、配置

具名提供方通过通常的模型插件接入。模型 ID 必须选取该账户实际可用的模型，不根据 API 目录推断订阅资格。

```python
from society0.kernel.models import model_plugin

models = model_plugin({
    'codex': {
        'endpoints': [{
            'id': 'chatgpt',
            'provider_type': 'siwc',
            'model': model_id,
            'account': 'default',
            'concurrency': 1,
            'timeout': 120,
        }],
        'max_attempts': 2,
    },
})
```

该插件需要 Thread 服务，驱动从 `context.require('models', 'models')['codex']` 取得 ModelProvider。Information、Actions、Thread 与 LLMDriver 继续由 Society0 管理。订阅接口不支持的请求字段会明确拒绝；温度和输出上限等研究条件应在选择 profile 时核对。支持范围及映射由 [models.py](../../src/society0/kernel/models.py) 维护。

工具发现和执行使用普通函数工具，服务适配生成 Responses namespace。完整上下文逐次发送，SDK 已识别的 opaque reasoning 字段随消息持久化。incomplete、error、failed 和未收到成功终态的流结束均保留诊断，部分工具输出不执行。

## 三、运行

[订阅示例](../../examples/core_next/codex_subscription.py) 提供公开 runner 使用的 `build(config)`，组合主体、信息与行动、订阅模型、Thread 和结果。配置中的 model、account 和源码身份按本次实际运行填写；输出使用新目录。

```sh
uv run python -m society0.kernel.runner \
  --factory examples.core_next.codex_subscription:build \
  --config /path/to/subscription-plan.json \
  --output /path/to/new-run
```

配置文件的最小形状如下，model 与 release 必须替换为实际值：

```json
{"model":"ACCOUNT_MODEL_ID","account":"default","release":"SOURCE_COMMIT","steps":2}
```

运行后通过观察接口读取当前状态、完整步骤与 Thread；HTTP 服务另外安装 `observe`。embedding 使用独立、明确配置的提供方，加入记忆插件时单独配置其服务和费用合同。若记忆提取也选择订阅模型，memory_plugin 显式传 extraction_options={}，替换一般提取器默认的 max_tokens=4096；订阅 profile 不接受该输出上限参数。提取策略、写入、召回继续分别配置。

本指南描述接入方式。当前实现的验证状态和开始测试的条件统一记录在 [TODO](TODO.md)，首次实际授权与模型调用应纳入对应验收。
