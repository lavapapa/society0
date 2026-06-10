# Tool to Action 系统迁移指导文档

## 概述

本文档指导如何将现有的Tool系统迁移到新的Action系统。核心变化包括：

1. **命名变更**：Tool → Action，ToolSet → ActionSet，ToolCall → ActionCall
2. **标签支持**：添加tags参数用于行动分类和筛选
3. **增强的筛选机制**：支持action_tags（包含）和exclude_tags（排除）
4. **基础行动保护**：具有'basic'标签的行动在未明确排除时始终可用

## 核心更改

### 1. 类名和方法名更改

| 原名称 | 新名称 | 说明 |
|--------|--------|------|
| `ToolSet` | `ActionSet` | 行动容器类 |
| `ToolCall` | `ActionCall` | 行动调用类 |
| `execute_tool_call_loop` | `execute_action_loop` | 推理引擎函数 |
| `add_tool` | `add_action` | 添加行动方法 |
| `call_tool` | `call_action` | 调用行动方法 |
| `get_openai_tools_schema` | `get_openai_actions_schema` | 获取OpenAI schema |

### 2. 方法签名更改

#### ActionSet.add_action()
```python
# 原版本
def add_tool(self, name: str, func: Callable, description: str, parameters: Dict[str, Any]):

# 新版本
def add_action(self, name: str, func: Callable, description: str, parameters: Dict[str, Any], tags: List[str] = None):
```

#### LLMAgent.instruct()
```python
# 原版本
async def instruct(self, instruction: str, context: Dict[str, Any] = None, tool_tags: Optional[List[str]] = None, ...):

# 新版本  
async def instruct(self, instruction: str, context: Dict[str, Any] = None, action_tags: Optional[List[str]] = None, exclude_tags: Optional[List[str]] = None, ...):
```

## 需要手动更改的文件

### 1. 测试文件

#### `/tests/test_cognitive_architecture.py`
需要更改的部分：
```python
# 第25行附近
from simengine.agent import init_global_milvus

# 第95行附近 - 更改方法名
agent.initialize_cognitive_system(...)  # 保持不变

# 第107行附近 - 更改属性名
print(f"   可用技能: {list(agent.tool_set.tools.keys())}")
# 改为：
print(f"   可用行动: {list(agent.action_set.actions.keys())}")
```

#### `/tests/test_memory_integration.py`
这个文件可能需要完全重写或删除，因为它使用了旧的接口。

### 2. Schedule文件中的Agent相关调用

#### `/src/simengine/schedule.py`
查找所有调用LLMAgent.execute_instruction的地方：
```python
# 查找类似这样的调用：
result = await agent.execute_instruction(instruction, context, is_memory=True)

# 如果需要使用新的action筛选功能，可以改为：
result = await agent.instruct(instruction, context, action_tags=['memory', 'basic'])
```

### 3. 任何直接使用ToolSet的代码

搜索整个代码库中的以下模式：
```bash
grep -r "ToolSet" src/
grep -r "tool_set" src/
grep -r "add_tool" src/
grep -r "call_tool" src/
```

对于每个找到的实例：
1. 将`ToolSet`改为`ActionSet`
2. 将`tool_set`改为`action_set`
3. 将`add_tool`改为`add_action`
4. 将`call_tool`改为`call_action`
5. 如果适用，添加tags参数

## 标签使用指南

### 预定义标签
- `"basic"`: 基础行动，在未明确排除时始终可用
- `"memory"`: 记忆相关行动
- `"reflection"`: 反思相关行动
- `"storage"`: 存储相关行动
- `"behavior"`: 行为相关行动

### 标签筛选示例
```python
# 只允许记忆相关行动
await agent.instruct("搜索记忆", action_tags=["memory"])

# 允许记忆和反思行动
await agent.instruct("分析情况", action_tags=["memory", "reflection"])

# 排除反思行动，但保留其他
await agent.instruct("执行任务", exclude_tags=["reflection"])

# 排除基础行动（慎用！）
await agent.instruct("受限模式", exclude_tags=["basic"])
```

## 向后兼容性

为了最小化破坏性更改，新系统提供了向后兼容的别名：
```python
# 这些别名在 agent/__init__.py 中定义
ToolSet = ActionSet
ToolCall = ActionCall  
execute_tool_call_loop = execute_action_loop
create_memory_tools = create_memory_actions
register_memory_tools_to_toolset = register_memory_actions_to_actionset
```

## 迁移检查清单

- [ ] 更新所有测试文件中的类名和方法名
- [ ] 检查schedule.py中的Agent调用
- [ ] 搜索并替换所有Tool相关的命名
- [ ] 为新的行动添加适当的tags
- [ ] 测试action_tags和exclude_tags功能
- [ ] 验证基础行动的保护机制工作正常
- [ ] 确认向后兼容性别名正常工作

## 验证步骤

1. **运行现有测试**：确保向后兼容性工作
2. **测试新功能**：验证标签筛选功能
3. **检查导入**：确保所有模块都能正确导入
4. **功能测试**：确保完整的认知架构仍然工作

如果在迁移过程中遇到问题，请检查：
1. 导入语句是否正确
2. 方法调用是否使用了新的参数名
3. 标签是否正确分配
4. 向后兼容性别名是否正确导入