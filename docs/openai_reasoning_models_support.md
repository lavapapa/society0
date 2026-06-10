# OpenAI 推理模型支持

本文档描述了 SimEngine 对 OpenAI 推理模型（如 o1 系列）的支持，这些模型具有显式的思考过程。

## 概述

OpenAI 推理模型在 API 响应中提供了额外的 `reasoning_content` 字段，包含模型的完整推理过程。SimEngine 现在能够：

1. **自动检测** 推理模型响应
2. **提取并存储** 推理过程和最终答案
3. **集成到记忆系统** 中供后续使用
4. **保持向后兼容** 与标准模型

## 核心特性

### 1. 响应格式识别

系统能够识别以下响应格式：

#### 标准模型响应
```json
{
  "content": "最终答案",
  "role": "assistant"
}
```

#### OpenAI 推理模型响应
```json
{
  "content": "最终答案",
  "reasoning_content": "详细的推理过程...",
  "role": "assistant"
}
```

### 2. 数据结构扩展

`LoopResult` 类新增了四个可选属性：

```python
@dataclass
class LoopResult:
    # ... 原有字段 ...

    # 新增字段 - 支持OpenAI推理模型
    reasoning_content: Optional[str] = None        # 原始推理内容
    thinking_process: List[Dict[str, Any]] = []   # 结构化的思考步骤
    has_reasoning: bool = False                   # 是否包含推理内容
    model_type: Optional[str] = None              # 模型类型（reasoning/standard）
```

#### 字段说明

- **reasoning_content**: 原始推理内容字符串，如果模型提供则包含完整的推理过程
- **thinking_process**: 推理步骤列表，每个步骤包含轮次、内容和元数据
- **has_reasoning**: 布尔标志，指示响应是否包含推理内容
- **model_type**: 模型类型，值为 "reasoning" 或 "standard"

### 3. 属性独立性

所有新增属性都是独立的，支持不同的使用场景：

- 有些模型可能只提供 `reasoning_content` 而不详细记录步骤
- 标准模型所有这些字段都为默认值
- 多轮对话中，不同轮次可能来自不同类型的模型

## 使用方法

### 基本使用

```python
from simengine.agent.agent_loop import execute_action_loop, ActionSet

# 正常使用，系统会自动检测和处理推理模型
result = await execute_action_loop(
    instruction="分析这个问题",
    action_set=action_set,
    system_prompt="你是一个分析助手",
    stages=["Observation", "Thinking", "Actions"],
    llm_call=llm_call_function
)

# 检查是否有推理内容
if result.has_reasoning:
    print(f"模型类型: {result.model_type}")
    print(f"推理过程: {result.reasoning_content}")
    print(f"思考步骤数: {len(result.thinking_process)}")
```

### LLMAgent 集成

```python
# 通过 LLMAgent.instruct() 获取推理信息
response = await agent.instruct("解决这个复杂问题")

# 检查推理信息
if response["has_reasoning"]:
    reasoning = response["reasoning_content"]
    steps = response["thinking_process"]
    print(f"推理过程: {reasoning}")
```

### 记忆系统集成

推理过程会自动集成到记忆系统中：

1. **摘要记忆**: 包含推理摘要的任务执行记忆
2. **详细记忆**: 单独存储的完整推理过程
3. **重要性加权**: 推理内容具有更高的记忆重要性（4.0-4.5）

## 向后兼容性

- ✅ **标准模型**: 行为完全不变，所有新字段保持默认值
- ✅ **现有代码**: 无需修改，新功能是可选的
- ✅ **API 兼容**: 返回结果结构向后兼容

## 配置选项

### 自动检测
系统会自动检测模型类型，无需额外配置。

### 手动覆盖（可选）
如果需要特殊处理，可以通过 LLM 调用函数返回特定的响应格式。

## 监控和调试

### 控制台输出
推理模型会在控制台输出推理过程预览：
```
🧠 Reasoning Content (Turn 1): 我需要思考这个问题...
```

### 日志记录
推理过程会记录在 Agent 的事件日志中，便于调试和分析。

## 性能考虑

1. **内存使用**: 推理内容会增加内存使用，但对于大多数应用来说是可接受的
2. **处理时间**: 提取推理内容的开销很小
3. **存储空间**: 记忆系统会额外存储推理过程，但重要性更高

## 示例：完整工作流

```python
async def reasoning_example():
    # 创建 Agent
    agent = LLMAgent("agent_001", world)

    # 执行指令
    response = await agent.instruct(
        instruction="分析这个问题并提供解决方案",
        retrieve_memory=True,
        save_memory=True
    )

    # 检查推理过程
    if response["has_reasoning"]:
        print("=== 推理过程分析 ===")
        print(f"模型类型: {response['model_type']}")
        print(f"推理内容长度: {len(response['reasoning_content'])} 字符")
        print(f"思考步骤数: {len(response['thinking_process'])}")

        # 显示推理过程摘要
        reasoning_preview = response['reasoning_content'][:200]
        print(f"推理预览: {reasoning_preview}...")

        # 分析思考步骤
        for i, step in enumerate(response['thinking_process']):
            print(f"步骤 {step['turn']}: {step['metadata']['model_type']}")
    else:
        print("使用标准模型，无显式推理过程")

    # 后续可以根据推理内容进行进一步分析
    return response
```

## 故障排除

### 常见问题

**Q: 为什么没有检测到推理内容？**
A: 请确认 LLM 响应中包含 `reasoning_content` 字段，并且模型支持 OpenAI 推理格式。

**Q: thinking_process 为空但 reasoning_content 有内容？**
A: 这可能是由于某些模型只提供推理内容而不进行步骤化记录。这是正常的行为。

**Q: model_type 为 None？**
A: 这通常表示响应没有被正确处理。检查 LLM 调用函数是否正确返回响应格式。

### 调试技巧

1. **启用详细日志**: 检查推理内容提取过程
2. **检查响应格式**: 确认 LLM 响应包含正确的字段
3. **验证字段独立性**: 检查每个属性是否正确设置

## 版本历史

- **v1.0**: 初始支持 OpenAI 推理模型
  - 添加推理内容提取
  - 扩展 LoopResult 数据结构
  - 集成记忆系统
  - 保持向后兼容性

---

*最后更新: 2025-10-28*