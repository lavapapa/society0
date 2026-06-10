# 记忆提取系统重构设计方案

**版本**: 1.0
**日期**: 2025-01-23
**作者**: Claude Code Analysis Team
**状态**: 设计完成，待实现

---

## 1. 问题背景与发现

### 1.1 原始问题的浮现

在项目运行过程中，我们观察到了一个严重的记忆重复问题。通过分析具体的对话历史记录，发现了如下的异常模式：

```
用户请求历史显示：
- 执行指令：请参与关于"食品安全"的社交媒体互动。你可以自主选择交互/不交互，以及方式。•结果：
- 执行指令：请参与关于"食品安全"的社交媒体互动。你可以自主选择交互/不交互，以及方式。•结果：
- 执行指令：请参与关于"食品安全"的社交媒体互动。你可以自主选择交互/不交互，以及方式。•结果：
...（重复出现10余次）
```

这表明记忆系统中存储了大量重复的模板化内容，严重影响了智能体的记忆质量和检索效率。

### 1.2 设计理念与现实的差距

通过深入分析项目文档和代码，我们发现了一个重要的设计理念与实际实现之间的差距：

**原始设计理念（来自 `docs/memeory_ref.md`）：**
- **双层记忆架构**：个体记忆 + 集体记忆
- **第一层记忆**：存储事实性记忆，通过在 loop 完成后追加 user message 来提取事实
- **事实性记忆**：具体的经验、行为、观察结果，应该是有血有肉的经历记录

**实际实现现状（`src/simengine/agent/core.py:826-831`）：**
```python
# 问题代码：模板化的记忆内容生成
memory_content = f"执行指令: {instruction}. 结果: {performative_output[:200]}"
```

这个差异直接导致了记忆质量的大幅下降。系统本应该存储丰富的事实性经历，却实际存储了干瘪的指令模板。

### 1.3 四个关键问题的深入分析

#### 问题1：角色视角的缺失

现有的记忆提取是客观的第三方视角，而真正的记忆应该是角色的主观视角。一个人记住的不是"执行了某个指令"，而是"我今天分享了对食品安全的看法，感觉很有意义"。

#### 问题2：结构化输出机制的误用

项目已经有了成熟的 `output_schema` 强制结构化输出机制（`finish_instruction`），但记忆提取没有借鉴这种成功的模式，导致提取结果不可控。

#### 问题3：向量数据库的背离

提取的记忆最终需要进入向量数据库进行语义检索，但现有的设计没有考虑到这一点，记忆内容不适合向量化。

#### 问题4：调用结果获取的错误理解

一个根本性的错误是：`performative_output[:200]` 只是最终表现性输出的摘要，根本没有包含完整的交互过程和具体执行的动作结果。真正的记忆应该包含"我做了什么、说了什么、得到了什么结果"的完整链条。

---

## 2. 核心设计原则

### 2.1 角色中心原则 (Role-Centered Principle)

记忆提取必须以角色的主观视角进行，而不是客观的第三方分析。每个记忆都应该是角色自言自语式的"我记得..."

**设计要求：**
- 记忆内容使用第一人称表述
- 包含角色的个人感受和思考
- 体现角色对事件的主观理解

### 2.2 事实完整性原则 (Fact Completeness Principle)

记忆必须包含完整的交互过程，而不是简单的指令和结果摘要。

**设计要求：**
- 记录原始指令的完整内容
- 记录角色的完整响应（文本 + 工具调用）
- 记录每个工具调用的具体结果
- 记录交互的时间序列和因果关系

### 2.3 结构化强制原则 (Structured Enforcement Principle)

必须完全借鉴项目已有的 `output_schema` 强制输出机制，确保记忆提取的结果结构化、可控。

**设计要求：**
- 创建独立的 `extract_memories` Action
- 使用强制 tool_choice 机制
- 支持失败重试逻辑
- 提供详细的 Schema 定义

### 2.4 向量化友好原则 (Vectorization-Friendly Principle)

提取的记忆内容必须适合向量化存储和语义检索。

**设计要求：**
- 记忆内容语言化、描述性
- 包含丰富的语义信息
- 适合相似度计算
- 支持基于内容的检索

---

## 3. 系统架构设计

### 3.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    LLMAgent.instruct()                      │
├─────────────────────────────────────────────────────────────┤
│  原有流程：                                                │
│  1. 记忆检索 → 2. 指令组装 → 3. 执行 loop → 4. 结果处理    │
│                                                             │
│  新增流程：                                                │
│  5. 记忆提取阶段（可选，受 interview 约束）                  │
│     ├─ 创建独立 ActionSet（仅包含 extract_memories）        │
│     ├─ 构建完整对话上下文                                    │
│     ├─ 强制执行记忆提取                                      │
│     └─ 返回结构化记忆结果                                    │
│                                                             │
│  6. 记忆存储（改进版）                                       │
│     ├─ 优先使用结构化记忆结果                                │
│     ├─ 回退使用改进的 fallback 内容                          │
│     └─ 存储到向量数据库                                      │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 记忆提取的触发机制

记忆提取作为一个可选阶段，在 `execute_action_loop` 主循环完成后触发。触发条件通过 `extract_memory` 参数控制。

**重要约束：**
- `interview` 方法必须硬编码禁止记忆提取（`extract_memory=False`）
- 只有 `instruct` 方法允许记忆提取
- 记忆提取不影响原有的主循环逻辑

### 3.3 ActionSet 的隔离策略

记忆提取阶段必须创建完全独立的 ActionSet，包含且仅包含 `extract_memories` 这一个 Action。这种设计确保：

1. **行为隔离**：记忆提取阶段不会调用任何其他工具
2. **专注性**：LLM 只能进行记忆提取，不能做其他事情
3. **可控性**：强制 LLM 调用记忆提取工具

### 3.4 对话历史的重建逻辑

记忆提取需要基于完整的对话历史，而不是碎片化的信息。重建逻辑包括：

1. **指令提取**：从第一轮对话中提取原始用户指令
2. **响应序列**：按时间顺序整理 LLM 的所有响应（文本 + 工具调用）
3. **结果映射**：将每个工具调用与其执行结果正确关联
4. **上下文构建**：将以上信息整合成连贯的对话描述

---

## 4. 详细技术规范

### 4.1 记忆提取 Schema 定义

```json
{
  "type": "object",
  "properties": {
    "memories": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "type": {
            "type": "string",
            "enum": ["episodic", "semantic", "emotional"],
            "description": "记忆类型：episodic-具体经历，semantic-知识概念，emotional-情感体验"
          },
          "content": {
            "type": "string",
            "description": "记忆的具体内容，使用第一人称，包含个人视角"
          },
          "importance": {
            "type": "number",
            "minimum": 0,
            "maximum": 5,
            "description": "重要性评分：0-琐碎日常，1-一般信息，2-有用信息，3-重要经历，4-关键信息，5-极其重要"
          },
          "personal_context": {
            "type": "string",
            "description": "个人背景和上下文，说明这个记忆对个人的意义"
          },
          "related_entities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "相关的人物、概念、地点、对象等实体"
          },
          "emotional_tone": {
            "type": "string",
            "enum": ["positive", "negative", "neutral", "mixed"],
            "description": "情感基调：positive-积极，negative-消极，neutral-中性，mixed-复杂"
          }
        },
        "required": ["type", "content", "importance"],
        "additionalProperties": false
      }
    }
  },
  "required": ["memories"],
  "additionalProperties": false
}
```

### 4.2 记忆类型定义规范

#### Episodic Memory（情景记忆）
**定义**：具体的个人经历和事件
**示例**：
- "今天我在社交媒体上分享了对食品安全的看法，引起了朋友们的讨论"
- "我第一次尝试使用健身房的器械，虽然有点紧张但感觉很兴奋"
- "和同事讨论项目时，我提出了一个新的解决方案，得到了认可"

#### Semantic Memory（语义记忆）
**定义**：抽象的知识、概念和理解
**示例**：
- "我现在理解了食品安全对健康的重要性，需要注意食材的选择"
- "学会了一个新的沟通技巧：在表达不同意见时要先肯定对方"
- "认识到团队合作中，及时沟通比默默工作更有效"

#### Emotional Memory（情感记忆）
**定义**：个人的情感体验和心理状态
**示例**：
- "分享观点时感到有些紧张，但看到朋友支持后变得更自信了"
- "完成困难任务后有强烈的成就感和满足感"
- "面对批评时感到沮丧，但冷静思考后学到了很多"

### 4.3 记忆提取提示词设计规范

记忆提取的提示词必须包含以下要素：

#### 角色设定部分
```
你是[角色名称]，正在回顾刚才的一段经历。请你以第一人称的视角，思考这次经历中哪些事情值得你记住。
```

#### 经历回顾部分
```
原始指令：
[完整的用户指令内容]

完整过程：
[按时间顺序描述：我做了什么 → 我说了什么 → 我调用了什么工具 → 得到了什么结果]
```

#### 思考引导部分
```
请思考以下问题：
1. 在这次经历中，你具体做了什么？学到了什么新东西？
2. 哪些信息对你个人来说很重要？为什么？
3. 什么经历值得你记住，以便未来参考？
4. 你有什么新的感受或理解？
5. 这些记忆对你的未来发展有什么意义？
```

#### 调用指导部分
```
请调用 extract_memories 工具来记录你的个人记忆。每个记忆都应该：
- 使用第一人称（"我"而不是"他/她"）
- 包含具体的事实和感受
- 评估重要性（0-5分）
- 说明相关的人和事
```

### 4.4 对话历史重建算法

#### 输入数据结构
```python
# LoopResult.full_history 的数据结构
[
    {
        "turn": 0,
        "request": {
            "messages": [
                {"role": "system", "content": "..."},
                {"role": "user", "content": "原始指令"},
                # ... 其他历史消息
            ],
            "tools": [...],  # 可用工具列表
            "tool_choice": {...}
        },
        "response": {
            "content": "LLM 的文本回复",
            "tool_calls": [
                {
                    "id": "call_123",
                    "function": {
                        "name": "tool_name",
                        "arguments": "{...}"
                    }
                }
            ]
        }
    },
    # ... 更多轮次
]
```

#### 重建算法步骤

**步骤1：提取原始指令**
```python
def extract_original_instruction(full_history):
    """从第一轮对话中提取用户指令"""
    first_turn = full_history[0]
    messages = first_turn["request"]["messages"]
    for msg in messages:
        if msg["role"] == "user":
            return msg["content"]
    return "指令内容未找到"
```

**步骤2：构建交互序列**
```python
def build_interaction_sequence(full_history):
    """构建完整的交互过程描述"""
    sequence = []

    for turn in full_history:
        turn_response = turn["response"]

        # 处理文本回复
        if turn_response.get("content"):
            sequence.append(f"🗣️ 我说：{turn_response['content']}")

        # 处理工具调用
        tool_calls = turn_response.get("tool_calls", [])
        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            tool_args = tool_call["function"]["arguments"]
            tool_result = find_tool_result(turn, tool_call["id"])

            sequence.append(f"🔧 我使用了：{tool_name}")
            sequence.append(f"📋 参数：{tool_args}")
            sequence.append(f"✅ 结果：{tool_result}")

    return sequence
```

**步骤3：结果查找算法**
```python
def find_tool_result(turn, tool_call_id):
    """在对话历史中查找工具调用的执行结果"""
    messages = turn["request"]["messages"]
    for msg in messages:
        if (msg["role"] == "tool" and
            msg.get("tool_call_id") == tool_call_id):
            return msg["content"]
    return "执行结果未记录"
```

### 4.5 强制执行机制设计

记忆提取采用两阶段强制执行机制，完全复制 `finish_instruction` 的成功模式：

#### 第一阶段：正常尝试
- 设置 `tool_choice` 强制指向 `extract_memories`
- 等待 LLM 自然调用工具
- 如果成功，解析结果并返回

#### 第二阶段：强制重试
- 如果第一阶段失败，追加强制提示："请必须调用 extract_memories 工具来完成记忆提取"
- 再次尝试调用
- 如果仍然失败，记录错误但不阻塞主流程

#### 错误处理策略
```python
# 错误处理的三个层次
1. 工具调用成功但解析失败 → 记录警告，尝试解析部分结果
2. 工具调用失败（两次尝试） → 记录错误，返回空的记忆列表
3. 系统异常 → 记录详细错误信息，确保不影响主流程
```

---

## 5. 调用链与集成规范

### 5.1 execute_action_loop 函数扩展

#### 新增参数定义
```python
async def execute_action_loop(
    instruction: str,
    action_set: ActionSet,
    system_prompt: str,
    stages: List[Union[str, Dict[str, Any]]],
    llm_call: Callable[[List[Dict]], Awaitable[Any]],
    act_prompt: str = DEFAULT_AGENT_ACT_PROMPT,
    max_turns: int = 4,
    default_stage_name: str = "default",
    context_provider: Optional[Callable] = None,

    # 新增参数
    extract_memory: bool = False,  # 是否启用记忆提取阶段
    agent_persona: str = "",        # 角色身份信息，用于构建记忆提取提示

) -> LoopResult:
```

#### 参数使用规范
- `extract_memory`: 控制是否在主循环后执行记忆提取
- `agent_persona`: 角色的身份描述，用于构建角色视角的提示词

#### 返回值扩展
```python
# LoopResult 新增字段
@dataclass
class LoopResult:
    # 现有字段保持不变...

    # 新增记忆相关字段
    memory_extraction_enabled: bool = False      # 是否启用了记忆提取
    extracted_memories: List[Dict[str, Any]] = field(default_factory=list)  # 提取到的记忆列表
    memory_extraction_success: bool = False      # 记忆提取是否成功
    memory_extraction_error: Optional[str] = None # 记忆提取的错误信息
```

### 5.2 LLMAgent.instruct 方法修改

#### 参数扩展
```python
async def instruct(
    self,
    instruction: str,
    context: Optional[Dict[str, Any]] = None,
    current_step: Optional[int] = None,
    action_tags: Optional[List[str]] = None,
    retrieve_memory: bool = True,
    save_memory: bool = True,

    # 新增参数
    extract_memory: bool = False,  # 是否进行记忆提取

    output_schema: Optional[Dict[str, Any]] = None,
    reasoning_stages: Optional[List[Dict[str, Any]]] = None,
    llm_call_override: Optional[Callable] = None,
    override_actionset: Optional[ActionSet] = None,
    max_turns: int = 4
) -> Dict[str, Any]:
```

#### 记忆存储逻辑重构

**新的记忆存储决策树：**

```python
# 记忆存储的三种情况
1. 启用记忆提取且成功 → 使用结构化记忆结果
2. 启用记忆提取但失败 → 使用改进的 fallback 内容
3. 未启用记忆提取 → 使用改进的 fallback 内容

# 改进的 fallback 内容生成
- 包含完整的交互过程
- 使用角色第一人称视角
- 保留重要的上下文信息
- 适合向量化的语言风格
```

### 5.3 Interview 方法的约束机制

#### 硬编码约束
```python
async def interview(self, question: str, **kwargs) -> Dict[str, Any]:
    """访谈方法，严格遵守记忆约束"""
    return await self.instruct(
        instruction=question,
        retrieve_memory=True,      # 允许读取现有记忆
        save_memory=False,         # 禁止写入新记忆
        extract_memory=False,      # 禁止记忆提取阶段
        action_tags=[],            # 禁止执行任何动作
        max_turns=2,              # 限制轮次
        **kwargs
    )
```

#### 约束原理说明
Interview 方法的设计目的是"纯净的问答"，不产生新的记忆。这个约束通过以下方式实现：

1. **save_memory=False**: 禁止任何形式的记忆存储
2. **extract_memory=False**: 禁止记忆提取阶段
3. **action_tags=[]**: 禁止执行需要权限的动作
4. **max_turns=2**: 限制对话轮次，避免复杂交互

### 5.4 记忆存储到向量数据库的规范

#### 结构化记忆的存储
```python
async def store_extracted_memories(self, memories: List[Dict], current_step: int):
    """将提取到的结构化记忆存储到向量数据库"""
    for memory_data in memories:
        await self._memory.add_episodic_memory(
            content=memory_data["content"],  # 第一人称的记忆内容
            timestamp=current_step,
            importance=memory_data["importance"],
            metadata={
                "memory_type": memory_data["type"],  # episodic/semantic/emotional
                "personal_context": memory_data["personal_context"],
                "related_entities": memory_data["related_entities"],
                "emotional_tone": memory_data["emotional_tone"],
                "extraction_method": "role_based_structured_extraction",
                "agent_id": self.id,
                "extraction_timestamp": datetime.now().isoformat()
            }
        )
```

#### Fallback 记忆的存储
```python
async def store_fallback_memory(self, loop_result: LoopResult, instruction: str, current_step: int):
    """存储改进的 fallback 记忆内容"""
    fallback_content = self._generate_comprehensive_fallback_content(loop_result, instruction)

    await self._memory.add_episodic_memory(
        content=fallback_content,
        timestamp=current_step,
        importance=3.0,  # 默认中等重要性
        metadata={
            "memory_type": "episodic",
            "extraction_method": "improved_fallback",
            "has_structured_extraction": False,
            "agent_id": self.id,
            "original_instruction": instruction
        }
    )
```

---

## 6. 实现步骤与迁移指南

### 6.1 第一阶段：数据结构扩展

#### 修改 LoopResult 类
**文件位置**: `src/simengine/agent/agent_loop.py`
**修改内容**: 添加记忆提取相关字段
**测试验证**: 确保现有测试通过，新字段默认值正确

#### 修改 execute_action_loop 函数签名
**文件位置**: `src/simengine/agent/agent_loop.py`
**修改内容**: 添加 extract_memory 和 agent_persona 参数
**向后兼容**: 新参数提供默认值，不影响现有调用

### 6.2 第二阶段：记忆提取核心逻辑

#### 实现记忆提取 Action 创建
**文件位置**: `src/simengine/agent/memory_extraction.py`（新建文件）
**功能**: 创建独立的记忆提取 ActionSet 和工具
**关键点**: 使用与 finish_instruction 相同的强制机制

#### 实现对话历史重建
**文件位置**: `src/simengine/agent/memory_extraction.py`
**功能**: 从 LoopResult.full_history 重建完整的对话上下文
**关键点**: 正确关联工具调用和执行结果

#### 实现强制提取机制
**文件位置**: `src/simengine/agent/memory_extraction.py`
**功能**: 两阶段强制执行，确保记忆提取成功
**关键点**: 复制 finish_instruction 的成功模式

### 6.3 第三阶段：集成到主工作流

#### 修改 execute_action_loop 主循环
**文件位置**: `src/simengine/agent/agent_loop.py`
**修改位置**: 主循环结束后，返回结果前
**关键点**: 记忆提取作为独立阶段，不影响主循环逻辑

#### 修改 LLMAgent.instruct 方法
**文件位置**: `src/simengine/agent/core.py`
**修改内容**: 添加 extract_memory 参数，修改记忆存储逻辑
**关键点**: 重构记忆存储决策树，优先使用结构化记忆

#### 确保 Interview 约束
**文件位置**: `src/simengine/agent/core.py`
**修改内容**: interview 方法硬编码禁用记忆提取
**关键点**: 维持 Interview 的纯净问答特性

### 6.4 第四阶段：测试与验证

#### 单元测试
- 记忆提取 Action 创建测试
- 对话历史重建算法测试
- Schema 验证测试
- 错误处理测试

#### 集成测试
- 完整的记忆提取流程测试
- Interview 约束验证测试
- 向量数据库存储测试
- 性能影响评估测试

#### 回归测试
- 确保现有功能不受影响
- 验证向后兼容性
- 检查边界情况处理

---

## 7. 关键技术决策说明

### 7.1 为什么选择两阶段强制执行？

借鉴了项目中 `finish_instruction` 的成功经验，该机制已经在大量实际使用中证明了其可靠性。两阶段设计提供了强制性和容错性的平衡：

1. **第一阶段的自然性**：允许 LLM 自然调用，保持交互的流畅性
2. **第二阶段的强制性**：确保关键任务必须完成，提供可靠性保证
3. **错误容忍性**：即使强制执行失败，也不阻塞主流程

### 7.2 为什么创建独立的 ActionSet？

记忆提取是一个特殊的认知阶段，与普通的工具调用有本质区别：

1. **专注性要求**：记忆提取需要 LLM 专注于反思和整理，不应被其他工具分散注意力
2. **行为隔离**：记忆提取阶段不应产生任何外部行为，只应进行内部认知
3. **可控性增强**：单一 Action 使得行为完全可预测和可控

### 7.3 为什么选择角色视角而不是客观视角？

这与人类记忆的本质特征相符：

1. **主观真实性**：人的记忆本质上是主观的，包含个人感受和理解
2. **情感丰富性**：角色视角的记忆包含情感色彩，更符合人类认知
3. **个性化特色**：每个角色会根据自己的特点和经历记住不同的事情

### 7.4 为什么需要重建完整的对话历史？

简化的指令+结果模式丢失了太多关键信息：

1. **过程价值**：完整的交互过程本身就有记忆价值
2. **上下文丰富性**：完整的对话提供了丰富的上下文信息
3. **因果关系**：可以清楚看到行为和结果之间的关联
4. **学习价值**：完整的记录有助于角色的学习和成长

---

## 8. 风险评估与缓解策略

### 8.1 性能风险

**风险描述**：记忆提取增加了额外的 LLM 调用，可能影响系统性能
**缓解策略**：
- 记忆提取作为可选功能，可根据需要开启
- 优化对话历史重建算法，减少计算开销
- 提供记忆提取频率控制，避免过度提取

### 8.2 向量存储压力

**风险描述**：结构化记忆可能增加向量数据库的存储压力
**缓解策略**：
- 实施记忆重要性过滤，只存储重要记忆
- 定期清理低价值的旧记忆
- 优化记忆内容的向量化策略

### 8.3 复杂性增加

**风险描述**：新的记忆提取机制增加了系统复杂性
**缓解策略**：
- 提供详细的文档和示例
- 实现充分的测试覆盖
- 保持向后兼容，支持渐进式迁移

### 8.4 质量控制风险

**风险描述**：自动提取的记忆质量可能不稳定
**缓解策略**：
- 提供记忆质量评估机制
- 支持人工审核和修正
- 实施记忆提取的反馈学习

---

## 9. 成功标准与验收条件

### 9.1 功能性标准

1. **记忆提取成功率**：在正常情况下，记忆提取成功率应达到 95% 以上
2. **结构化输出合规性**：提取的记忆 100% 符合预定义的 Schema 规范
3. **向量化兼容性**：所有提取的记忆都适合向量化存储和检索
4. **Interview 约束遵守**：Interview 方法绝对不产生任何新记忆

### 9.2 质量性标准

1. **角色视角一致性**：所有记忆都使用第一人称，体现角色特色
2. **事实完整性**：记忆内容包含完整的交互过程和上下文
3. **语义丰富性**：记忆内容语言化、描述性，适合语义理解
4. **重要性合理性**：记忆的重要性评分符合直觉和逻辑

### 9.3 性能标准

1. **响应时间**：记忆提取阶段的额外延迟不超过 2 秒
2. **资源消耗**：内存和 CPU 使用增加不超过 20%
3. **存储效率**：向量数据库的存储增长控制在合理范围内
4. **检索性能**：记忆检索的响应时间不受显著影响

### 9.4 兼容性标准

1. **向后兼容**：现有代码无需修改即可继续工作
2. **API 稳定性**：所有现有接口保持稳定
3. **数据兼容性**：现有的记忆数据继续可用
4. **配置兼容性**：现有配置文件无需修改

---

## 10. 未来扩展方向

### 10.1 记忆质量自动评估

未来可以实现基于机器学习的记忆质量自动评估机制，根据记忆的完整性、准确性、重要性等指标自动评分和过滤。

### 10.2 个性化记忆提取提示

根据角色的个性特点、历史行为模式，动态调整记忆提取的提示词和重点，提取更加个性化的记忆内容。

### 10.3 记忆关联与推理

实现记忆之间的关联分析，支持基于记忆的推理和预测，让智能体能够从记忆中发现模式和规律。

### 10.4 多模态记忆支持

扩展记忆系统以支持图片、音频、视频等多模态记忆的提取、存储和检索。

---

**结语**

本设计方案通过深入分析现有问题，借鉴项目中的成功经验，提供了一个完整、可行的记忆提取系统重构方案。该方案不仅解决了当前的问题，还为未来的功能扩展奠定了基础。

通过严格的架构设计、详细的实现规范和全面的测试策略，我们有信心这个方案能够显著提升智能体的记忆质量，为更高级的认知功能提供支撑。

---

**文档版本历史**
- v1.0 (2025-01-23): 初始设计完成，包含完整的技术规范和实现指导

**相关文档**
- `docs/memeory_ref.md`: 记忆系统的原始设计理念
- `docs/memory_system_design.md`: 当前的记忆系统架构设计
- `docs/finish_instruction_enhancements.md`: 强制结构化输出机制的实现参考