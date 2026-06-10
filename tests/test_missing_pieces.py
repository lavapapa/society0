#!/usr/bin/env python3
"""
综合集成测试 - 验证所有遗漏点的修复

测试包括：
1. World-FunctionRegistry 连接
2. Action 装配流程  
3. FoV 函数调用
4. LLMAgent 完整认知流程
5. ActionSet 的正确装配和使用
"""

import sys
import os
import asyncio
import tempfile
from unittest.mock import AsyncMock

# Add project path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

def test_complete_integration():
    """测试完整的集成流程"""
    print("=== 综合集成测试 ===")
    
    import tempfile
    from simengine.core_data import World
    from simengine.function_registry import FunctionRegistry
    from simengine.schedule import Schedule
    from simengine.context_stack import ContextStack
    
    # Create temporary log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # 1. 创建基础设施
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "llm")
        world.add_agent_data("bob", "teacher", "rule")
        
        # 2. 创建 FunctionRegistry 并注册函数
        registry = FunctionRegistry()
        
        # 注册 FoV 函数  
        @registry.env.fov("classroom_status")
        def classroom_status(agent, env):  # 函数名需要和调用时一致
            return {
                "students_present": 25,
                "teacher_present": True,
                "current_lesson": "Math"
            }
        
        # 注册 Agent Action
        @registry.agent.action("study_action")
        async def study_action(agent_ids, world, params):
            subject = params.get("subject", "general")
            results = []
            for agent_id in agent_ids:
                agent = world.get_agent(agent_id)
                current_knowledge = agent.state.get("knowledge", {})
                current_knowledge[subject] = current_knowledge.get(subject, 0) + 1
                agent.state["knowledge"] = current_knowledge
                results.append(f"{agent_id} studied {subject}")
            return results
        
        # 注册 Agent Rule
        @registry.agent.rule("gain_experience")
        async def gain_experience_rule(agent, world, params):
            exp_gained = params.get("amount", 10)
            current_exp = agent.state.get("experience", 0)
            agent.state["experience"] = current_exp + exp_gained
            return f"{agent.id} gained {exp_gained} experience"
        
        # 3. 创建 Schedule 配置，测试 instruct 操作
        schedule_config = {
            "nodes": [
                {
                    "id": "test_instruct_node",
                    "selector": {
                        "type": "by_archetype",
                        "archetype": "llm"
                    },
                    "operators": [
                        {
                            "type": "instruct",
                            "instruction": "Please introduce yourself and tell me what you know about the classroom",
                            "fovs": ["classroom_status"],  # 使用装饰器中指定的名称
                            "action_tags": ["memory", "registry"],
                            "is_memory": False  # 关闭记忆以简化测试
                        }
                    ],
                    "dependencies": []
                }
            ]
        }
        
        # 4. 创建 Schedule 并设置上下文
        schedule = Schedule(schedule_config, registry)
        world.set_context_stack(ContextStack().push_step("step_0"))
        
        # 5. 设置 LLMAgent 的认知系统
        alice = world.get_agent("alice")
        
        # Mock LLM call function
        async def mock_llm_call(payload):
            messages = payload.get("messages", [])
            user_message = next((msg for msg in messages if msg.get("role") == "user"), {})
            user_content = user_message.get("content", "")
            
            # 模拟 LLM 回应，包含对 FoV 信息的引用
            response_content = f"Hello! I'm Alice, a student. I can see that there are 25 students present in the classroom, the teacher is here, and we're currently in a Math lesson. This is very helpful information!"
            
            return {
                "role": "assistant",
                "content": response_content,
                "tool_calls": []  # 暂时不测试 tool calls
            }
        
        alice.initialize_cognitive_system(
            persona={"name": "Alice", "role": "student"},
            memory=None,  # 暂时不使用记忆系统
            llm_call=mock_llm_call
        )
        
        # 6. 设置 FunctionRegistry（在装配 ActionSet 之前）
        world.set_function_registry(registry)
        
        # 7. 装配 ActionSet
        world.assemble_agent_actionset(alice)
        
        # 8. 执行测试
        async def run_integration_test():
            result = await schedule.execute_step(world)
            return result
        
        result = asyncio.run(run_integration_test())
        
        # 8. 验证结果
        assert result["nodes_executed"] == 1, f"应该执行 1 个节点，实际 {result['nodes_executed']}"
        
        # 验证 registry 被正确设置
        print(f"   FunctionRegistry 是否设置: {hasattr(world, '_function_registry')}")
        if hasattr(world, '_function_registry'):
            print(f"   Registry env_fovs: {list(world._function_registry.env_fovs.keys())}")
        
        # 验证 Alice 有 ActionSet
        assert hasattr(alice, '_actionset'), "Alice 应该有 _actionset"
        assert alice._actionset is not None, "ActionSet 不应该为空"
        
        # 调试信息：检查 ActionSet 装配过程
        print(f"   Agent memory: {alice._memory}")
        print(f"   Registry agent_actions: {list(registry.agent_actions.keys())}")
        if hasattr(world, '_function_registry'):
            print(f"   World registry agent_actions: {list(world._function_registry.agent_actions.keys())}")
        
        assert len(alice._actionset.actions) > 0, f"ActionSet 应该有 actions，实际有 {len(alice._actionset.actions)}"
        
        # 验证 ActionSet 中包含预期的 actions
        action_names = list(alice._actionset.actions.keys())
        print(f"   装配的 Actions: {action_names}")
        
        # 应该包含 registry actions（因为 memory=None，所以不包含 memory actions）
        expected_actions = ["study_action"]  # 只检查 registry actions
        for expected_action in expected_actions:
            assert expected_action in action_names, f"缺少预期的 action: {expected_action}"
        
        world.event_logger.close()
        
        print("✅ 综合集成测试通过")
        print(f"   节点执行数: {result['nodes_executed']}")
        print(f"   装配的 Actions 数量: {len(alice._actionset.actions)}")
        print(f"   Actions: {', '.join(action_names)}")
        return True
        
    except Exception as e:
        print(f"❌ 综合集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_fov_integration():
    """专门测试 FoV 功能的集成"""
    print("=== FoV 集成测试 ===")
    
    import tempfile
    from simengine.core_data import World
    from simengine.function_registry import FunctionRegistry
    
    # Create temporary log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # 创建基础设施
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "llm")
        
        # 创建 FunctionRegistry 并注册 FoV
        registry = FunctionRegistry()
        
        @registry.env.fov("test_fov")
        def test_fov(agent, env):
            return {
                "agent_id": agent.id,
                "agent_type": agent.type,
                "env_type": env.type,
                "timestamp": "2023-01-01"
            }
        
        # 设置 registry 到 world
        world.set_function_registry(registry)
        
        # Mock LLM call
        async def mock_llm_call(payload):
            return {
                "role": "assistant", 
                "content": "I received the FoV information successfully.",
                "tool_calls": []
            }
        
        # 初始化 Alice
        alice = world.get_agent("alice")
        alice.initialize_cognitive_system(
            persona={"name": "Alice", "role": "student"},
            memory=None,
            llm_call=mock_llm_call
        )
        
        async def test_fov_call():
            # 直接测试 FoV 调用
            result = await world.instruct_agent(
                agent_id="alice",
                instruction="Test FoV functionality",
                fovs=["test_fov"]
            )
            return result
        
        result = asyncio.run(test_fov_call())
        
        # 验证结果
        assert result["status"] == "success", f"FoV 测试失败: {result.get('error', 'Unknown error')}"
        
        world.event_logger.close()
        
        print("✅ FoV 集成测试通过")
        print(f"   Status: {result['status']}")
        return True
        
    except Exception as e:
        print(f"❌ FoV 集成测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def main():
    """运行所有遗漏点修复测试"""
    print("🚀 开始遗漏点修复测试\n")
    
    tests = [
        test_complete_integration,
        test_fov_integration,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
                print()
            else:
                failed += 1
                print(f"❌ {test.__name__} 测试失败\n")
        except Exception as e:
            failed += 1
            print(f"❌ {test.__name__} 测试异常: {e}")
            import traceback
            traceback.print_exc()
            print()
    
    print(f"📊 测试结果：通过 {passed}/{len(tests)} 个测试")
    
    if failed == 0:
        print("🎉 所有遗漏点修复测试通过！集成完整无缺。")
        return True
    else:
        print(f"💥 有 {failed} 个测试失败，需要进一步修复。")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)