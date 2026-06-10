# 设计文档：通用环境规则 (Environment Rule) 算子

**作者:** Gemini

**日期:** 2025年9月20日

**状态:** 提案

## 1. 摘要

为了增强 `simengine` 的通用性和易用性，本文档提议设计并实现一个全新的、作为一等公民的“环境规则 (Environment Rule)”算子。当前框架缺乏一个清晰、直接的机制来执行作用于整个环境的全局性、确定性逻辑。此提案旨在填补这一空白，为研究者提供一种强大而优雅的方式，在仿真计划的特定节点执行“上帝视角”的干预或世界演化逻辑。

## 2. 核心动机与设计目标

- **动机:** 在复杂的社会科学仿真中，经常需要在不受任何智能体主观决策影响的情况下，对环境施加一个全局性的变更。例如：实施一项新的税收政策、模拟一次自然灾害、或像我们的实验一样，对特定信息进行干预标记。当前通过 `custom` 算子实现这一功能路径不清晰且存在缺陷。
- **设计目标:**
    1.  **明确概念:** 将“环境规则”提升为与“动作(Action)”、“行为(Behavior)”并列的核心概念。
    2.  **强大而安全:** 赋予规则函数完全访问 `World` 对象的能力，同时确保其调用方式是安全和可预测的。
    3.  **提升开发者体验:** 规则的定义应符合 Pythonic 的编码习惯，支持具名参数和默认值，而不是强制开发者在函数内部解析 `params` 字典。
    4.  **声明式调用:** 规则的调用应在 `schedule.yaml` 中以清晰、声明式的方式进行。

## 3. 详细设计方案

### 3.1. 规则函数的定义 (开发者视角)

规则是一个简单的 Python `async` 函数，它接收 `World` 对象作为第一个参数，后续参数为自定义的、带类型的具名关键字参数。函数通过 `@rule` 装饰器进行标记。

**示例:**
```python
# 在 env.py 或 behaviors.py 中定义
from simengine.core_data import World
from simengine.decorators import rule # 假设的装饰器路径

@rule
async def apply_intervention_tags(world: World, 
                                  hashtag: str, 
                                  intervention_rate: float = 1.0, 
                                  tag_to_apply: str = "flagged") -> dict:
    """
    一个典型的环境规则，用于给帖子打标签。
    它拥有语义化的参数和默认值。
    """
    posts_to_check = []
    # 1. 直接访问 world 对象，获取所有帖子
    all_posts = world.environment_data.get("state", {}).get("posts", {})
    
    # 2. 执行核心逻辑
    for post_id, post_data in all_posts.items():
        if hashtag in post_data.get("content", ""):
            # ... 实现幂等性检查和随机抽样逻辑 ...
            pass

    # 3. 返回一个包含执行结果的字典
    return {"tagged_count": len(posts_to_check), "rate": intervention_rate}
```

### 3.2. 规则的注册

规则的注册将支持两种方式，以提供最大的灵活性。

1.  **环境内自动发现:**
    *   **机制:** `SimEngine` 在初始化时，其 `_register_environment_functions` 方法会检查环境实例的所有方法。如果一个方法被 `@rule` 装饰器标记，它将被自动注册。
    *   **实现:** 此方法需进行修改，将发现的规则函数及其签名存入 `registry.rules` 字典中。

2.  **外部手动注册:**
    *   **机制:** `SimEngine` 的主注册器 `engine.register` 将暴露一个名为 `rules` 的字典。用户可以在他们的主启动脚本（如 `main.py`）中直接添加外部定义的规则函数。
    *   **示例:** `engine.register.rules['my_external_rule'] = my_external_rule_func`

### 3.3. 规则的编译与调用 (框架核心)

这是实现“智能参数映射”的关键步骤。

-   **文件:** `src/simengine/schedule.py`
-   **核心逻辑:** 在 `StepFlow._compile_operator` 方法中，新增对 `type: rule` 的处理分支。

**编译流程:**
1.  当 `operator_type` 为 `rule` 时，从 `operator_config` 中获取规则的名称 (`name`)。
2.  在 `self.registry.rules` 中查找该名称，获取其函数对象 `func` 和签名 `sig`。
3.  **动态创建包装器 (Wrapper):** 创建一个内部的 `async def rule_wrapper(world: World, params: dict)` 函数。这个包装器才是最终被算子执行的函数。
4.  **包装器内部实现:**
    a.  创建一个空字典 `mapped_args` 用于存放最终要传递给用户函数的参数。
    b.  遍历用户规则函数签名 `sig` 中的所有参数（跳过 `world`）。
    c.  对于每一个参数 `p`：
        i.  如果 `p.name` 存在于 `params` 字典中，则 `mapped_args[p.name] = params[p.name]`。
        ii. 如果 `p.name` 不在 `params` 中，但 `p.default` 不是空值（即有默认值），则 `mapped_args[p.name] = p.default`。
        iii. 如果既不在 `params` 中，又没有默认值，则这是一个错误，应抛出异常，提示“缺少必要的规则参数”。
    d.  **调用用户函数:** 使用解包语法调用原始的、用户定义的规则函数：`result = await func(world, **mapped_args)`。
    e.  返回 `result`。
5.  `_compile_operator` 方法最终返回这个 `rule_wrapper` 函数。

### 3.4. 规则的使用 (`schedule.yaml`)

经过上述实现后，在 `schedule.yaml` 中使用规则变得非常直观和声明式。

**示例:**
```yaml
- id: intervention_phase
  # 规则通常作用于整个环境，因此选择器是 environment
  selector: 
    type: environment
  operator:
    type: rule                  # <-- 清晰地指明算子类型
    name: apply_intervention_tags # <-- 规则函数的Python函数名
    params:                    # <-- 框架会自动映射这些参数
      hashtag: "#some_hashtag"
      intervention_rate: 0.5
      tag_to_apply: "flagged_by_admin"
```

## 4. 结论

该设计方案通过引入 `@rule` 装饰器、统一的注册流程和带有智能参数映射的 `rule` 算子，为 `simengine` 框架增加了一个强大、灵活且符合工程学直觉的全局干预机制。它将复杂的参数处理逻辑封装在框架内部，同时为研究者提供了清晰、简单、强大的函数定义和使用体验，是完善仿真引擎核心能力的关键一步。
