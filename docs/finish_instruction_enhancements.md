# Finish Instruction 增强功能文档

**版本**: 1.0
**日期**: 2025-01-20
**状态**: 已实现

## 概述

本文档记录了对 `finish_instruction` 工具的两项重要增强，旨在提高结构化输出的可靠性和严格性。

## 背景问题

1. **LLM 不传参数问题**：在实际使用中发现，即使提供了正确的 schema，LLM 在调用 `finish_instruction` 工具时有时会不传递任何参数，导致结构化输出失败。

2. **Schema 灵活性问题**：希望启用 strict 模式来强制 LLM 严格按照 schema 输出，但不同 LLM 对 strict 模式的支持程度不同。

## 解决方案

### 1. 自动添加 required 字段

**实现位置**: `simengine.agent.core.LLMAgent._enhance_output_schema()`

**功能描述**：
- 当 `output_schema` 中没有 `required` 字段时，自动将所有顶层属性字段添加到 `required` 列表中
- 仅处理顶层字段，不涉及嵌套结构
- 保持原有 `required` 字段不变（如果已存在）

**示例**：
```python
# 输入 schema
input_schema = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "priority": {"type": "string"},
        "next_steps": {"type": "array"}
    }
}

# 输出 schema（自动添加 required）
output_schema = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "priority": {"type": "string"},
        "next_steps": {"type": "array"}
    },
    "required": ["analysis", "priority", "next_steps"]  # 自动添加
}
```

### 2. Strict 模式重试机制

**实现位置**: `simengine.agent.core.LLMAgent._call_with_strict_retry()`

**功能描述**：
- 首次尝试调用时，为 `finish_instruction` 工具添加 `strict: true` 参数
- 如果调用失败，自动重试一次（不使用 strict 模式）
- 采用简单重试策略，不区分错误类型
- 仅在强制执行轮次中使用此机制

**重试逻辑**：
```python
# 第一次尝试：启用 strict
try:
    enhanced_request = copy.deepcopy(request)
    for tool in enhanced_request.get("tools", []):
        if tool.get("function", {}).get("name") == "finish_instruction":
            tool["function"]["parameters"]["strict"] = True
            break
    return await effective_llm_call(enhanced_request)
except Exception as e:
    # 第二次尝试：不使用 strict
    logger.info(f"Strict mode failed, retrying without strict: {e}")
    return await effective_llm_call(request)
```

## 修改的代码位置

### 1. 新增方法

**文件**: `src/simengine/agent/core.py`

- `_enhance_output_schema()` (第 378-396 行)
- `_call_with_strict_retry()` (第 398-422 行)

### 2. 修改的现有代码

**文件**: `src/simengine/agent/core.py`

- **第 588-590 行**：在 `finish_instruction` Action 创建时使用增强的 schema
  ```python
  # 增强 output_schema
  enhanced_schema = self._enhance_output_schema(output_schema)
  ```

- **第 657-663 行**：强制执行轮次使用 strict 重试机制
  ```python
  # 最后一次LLM调用（使用 strict 重试机制）
  final_response = await self._call_with_strict_retry(effective_llm_call, {
      "messages": messages,
      "tools": available_actionset.get_openai_actions_schema(),
      "tool_choice": {"type": "function", "function": {"name": "finish_instruction"}}
  })
  ```

## 使用效果

### 1. 提高可靠性
- 自动 `required` 字段确保 LLM 必须提供所有参数
- 减少因参数缺失导致的结构化输出失败

### 2. 兼容性保证
- Strict 模式重试机制确保在不支持 strict 的模型上也能正常工作
- 不会破坏现有的功能和行为

### 3. 透明性
- 通过日志记录 strict 模式的失败和重试情况
- 开发者可以了解何时发生了重试

## 注意事项

1. **仅顶层字段**：自动 `required` 功能只处理 schema 的顶层属性字段
2. **简单重试**：任何异常都会触发重试，不做错误类型区分
3. **范围限制**：strict 重试机制仅在 `finish_instruction` 的强制执行轮次中使用
4. **向后兼容**：现有的 schema 如果已经定义了 `required` 字段，不会被覆盖

## 测试验证

通过测试验证了以下场景：
- ✅ 没有 `required` 字段的 schema 自动添加所有字段
- ✅ 已有 `required` 字段的 schema 保持不变
- ✅ Strict 模式成功时只调用一次
- ✅ Strict 模式失败时自动重试一次（不使用 strict）

这些增强功能显著提高了 `finish_instruction` 工具的可靠性和兼容性。