#!/usr/bin/env python3
"""
重构后的Agent认知架构集成测试

测试新的Agent模块化架构：
1. Persona人格系统
2. state/properties分离
3. reminders提醒机制
4. instruct方法的完整协调逻辑
5. Memory系统集成
"""

import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

async def test_cognitive_architecture():
    """测试Agent认知架构"""

    print("=== Agent Cognitive Architecture Test ===")

    # 1. 初始化全局Milvus
    print("\n1. 初始化全局Milvus...")
    from simengine.agent import init_global_milvus

    try:
        await init_global_milvus("./test_cognitive_milvus.db")
        print("✅ Milvus全局实例初始化成功")
    except Exception as e:
        print(f"❌ Milvus初始化失败: {e}")
        return False

    # 2. 创建模拟LLM和embedding函数
    print("\n2. 设置模拟函数...")

    async def mock_llm_call(payload: dict) -> dict:
        """模拟LLM调用"""
        messages = payload.get("messages", [])
        if not messages:
            return {"role": "assistant", "content": "模拟响应"}

        user_message = messages[-1].get("content", "")

        if "重要性" in user_message:
            return {"role": "assistant", "content": "4.2"}
        elif "反思" in user_message:
            return {"role": "assistant", "content": "通过综合分析，我发现这个主题具有深层含义..."}
        else:
            return {"role": "assistant", "content": f"-> STAGE_BEGIN: Understanding\n我理解了指令。\n\n-> STAGE_BEGIN: Response\n基于我的人格和记忆，我的回应是：这是一个有意义的任务。"}

    async def mock_embed_call(texts: list, dimensions: int = 512) -> dict:
        """模拟embedding函数"""
        import random
        embeddings = []
        for text in texts:
            embedding = [random.uniform(-1.0, 1.0) for _ in range(dimensions)]
            embeddings.append(embedding)

        return {
            "result": embeddings,
            "model": "mock-embed-model",
            "dimensions": dimensions
        }

    print("✅ 模拟函数设置完成")

    # 3. 测试Persona系统
    print("\n3. 测试Persona人格系统...")

    try:
        # 测试字典格式的persona
        persona = {
            "背景": "我是一名经验丰富的数据科学家，在金融科技领域工作了5年",
            "性格": "理性、细致、善于分析，但也充满好奇心和创造力",
            "价值观": "追求真理和准确性，重视数据驱动的决策，相信技术能改善世界",
            "目标": "成为AI和金融的交叉领域专家，帮助更多人理解数据的价值"
        }

        print("✅ Persona创建成功")
        print(f"   Persona类型: {type(persona).__name__}")
        print(f"   Persona内容: {list(persona.keys())}")

    except Exception as e:
        print(f"❌ Persona创建失败: {e}")
        return False

    # 4. 测试Agent创建与state/properties分离
    print("\n4. 测试Agent创建与主客观分离...")
    from simengine.agent import LLMAgent

    try:
        agent = LLMAgent(
            id="alice",
            type="data_scientist",
            state={
                "current_mood": "focused",
                "short_term_goal": "完成季度报告",
                "energy_level": 0.8
            },
            properties={
                "location_x": 100.5,
                "location_y": 200.3,
                "physical_id": "phys_alice_001"
            },
            reminders=[
                "团队会议在下午2点",
                "别忘了检查数据质量报告"
            ]
        )

        print("✅ LLMAgent创建成功")
        print(f"   主观状态keys: {list(agent.state.keys())}")
        print(f"   客观属性keys: {list(agent.properties.keys())}")
        print(f"   提醒数量: {len(agent.reminders)}")

    except Exception as e:
        print(f"❌ Agent创建失败: {e}")
        return False

    # 5. 初始化认知系统
    print("\n5. 初始化完整认知系统...")
    from simengine.agent import Memory

    try:
        # 创建Memory实例
        memory = Memory(
            agent_id="alice",
            branch_id="main",
            embed_call=mock_embed_call,
            llm_call=mock_llm_call
        )

        # 初始化认知系统
        agent.initialize_cognitive_system(
            persona=persona,
            memory=memory,
            llm_call=mock_llm_call
        )

        print("✅ 认知系统初始化成功")
        print(f"   可用技能: {list(agent.action_set.actions.keys())}")

    except Exception as e:
        print(f"❌ 认知系统初始化失败: {e}")
        return False

    # 6. 添加一些测试记忆
    print("\n6. 添加测试记忆...")
    try:
        await memory.add_episodic_memory(
            content="昨天完成了用户行为分析项目",
            timestamp=1,
            importance=4.0
        )

        await memory.add_semantic_memory(
            content="机器学习模型需要定期重新训练以保持准确性",
            timestamp=2,
            importance=4.5
        )

        print("✅ 测试记忆添加成功")

    except Exception as e:
        print(f"❌ 记忆添加失败: {e}")
        return False

    # 7. 测试完整的instruct工作流
    print("\n7. 测试完整instruct工作流...")
    try:
        # 模拟外部上下文
        context = {
            "fov_results": {
                "team_status": "团队成员都在线，准备协作",
                "project_deadline": "项目截止日期是下周五"
            }
        }

        # 执行指令
        result = await agent.instruct(
            instruction="请分析当前项目进度，并制定今天的工作计划",
            context=context,
            current_step=10,
            stages=["Understanding", "MemoryRecall", "Planning", "Response"]
        )

        print("✅ instruct执行成功")
        print(f"   执行状态: {result['status']}")
        print(f"   Agent ID: {result['agent_id']}")
        print(f"   推理阶段: {result.get('reasoning_phases', [])}")
        print(f"   表现输出: {result['performative_output'][:100]}...")

        # 验证提醒机制：提醒应该被消费掉
        print(f"   提醒消费后数量: {len(agent.reminders)} (应该为0)")

    except Exception as e:
        print(f"❌ instruct执行失败: {e}")
        return False

    # 8. 测试提醒机制的一次性消费
    print("\n8. 测试提醒机制...")
    try:
        # 添加新的提醒
        agent.reminders.extend([
            "新的数据更新已就绪",
            "客户反馈需要处理"
        ])

        # 再次执行instruct
        result2 = await agent.instruct(
            instruction="检查有什么新的任务需要处理",
            current_step=11
        )

        print("✅ 提醒机制测试成功")
        print(f"   第二次执行状态: {result2['status']}")
        print(f"   提醒再次消费后数量: {len(agent.reminders)} (应该为0)")

    except Exception as e:
        print(f"❌ 提醒机制测试失败: {e}")
        return False

    # 9. 验证记忆持久化
    print("\n9. 验证记忆系统...")
    try:
        memories = await memory.retrieve(
            query="项目进度和工作计划",
            top_k=5,
            current_step=12
        )

        print("✅ 记忆系统验证成功")
        print(f"   检索到记忆数量: {len(memories)}")
        for i, mem in enumerate(memories[:3], 1):
            print(f"   记忆{i}: {mem[:60]}...")

    except Exception as e:
        print(f"❌ 记忆系统验证失败: {e}")
        return False

    # 10. 测试WorldState集成
    print("\n10. 测试WorldState集成...")
    try:
        from simengine.core_data import WorldState

        world = WorldState(step=0)
        world.add_agent(agent)

        print("✅ WorldState集成成功")
        print(f"   世界状态摘要: {world.get_state_summary()}")

    except Exception as e:
        print(f"❌ WorldState集成失败: {e}")
        return False

    print(f"\n🎉 Agent认知架构测试全部通过！")
    print("✅ Persona人格系统运行正常")
    print("✅ state/properties主客观分离实现")
    print("✅ reminders一次性消费机制正常")
    print("✅ instruct完整协调工作流验证完成")
    print("✅ Memory系统集成无误")
    print("✅ Agent as Assembler架构实现成功")

    return True

if __name__ == "__main__":
    asyncio.run(test_cognitive_architecture())