#!/usr/bin/env python3
"""
内置 Operators 综合测试

验证所有内置 operators 在新统一架构下正常工作：
1. Rule operator - 执行 agent 和 environment 规则
2. Instruct operator - 执行 LLM agent 指令（模拟）
"""

import sys
import os
import asyncio
import tempfile

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

def test_rule_operator():
    """测试 Rule operator"""
    print("=== 测试: Rule Operator ===")
    
    import asyncio
    import tempfile
    from simengine.core_data import World
    from simengine.function_registry import FunctionRegistry
    from simengine.schedule import Schedule
    from simengine.context_stack import ContextStack
    
    # 创建临时日志文件
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # 创建 World 和数据
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "rule")
        
        # 创建函数注册表和规则
        registry = FunctionRegistry()
        
        @registry.agent.rule("增加金币规则")
        async def add_gold_rule(agent, world, params):
            amount = params.get("amount", 10)
            current = agent.state.get("gold", 0)
            agent.state["gold"] = current + amount
            return f"为 {agent.id} 增加了 {amount} 金币"
            
        @registry.env.rule("环境更新规则")
        async def update_weather_rule(environment, world, params):
            environment.state["weather"] = params.get("weather", "sunny")
            environment.state["updated_by"] = "rule_operator"
            return f"天气更新为 {params.get('weather', 'sunny')}"
        
        # 测试 agent rule
        agent_config = {
            "nodes": [{
                "id": "agent_rule_test",
                "selector": {"type": "all_agents"},
                "operators": [{
                    "type": "rule",
                    "rule_name": "add_gold_rule",
                    "amount": 50
                }],
                "dependencies": []
            }]
        }
        
        schedule = Schedule(agent_config, registry)
        world.set_context_stack(ContextStack().push_step("step_0"))
        
        async def run_agent_test():
            return await schedule.execute_step(world)
        
        result = asyncio.run(run_agent_test())
        
        # 验证 agent rule 结果
        alice = world.get_agent("alice")
        assert alice.state["gold"] == 50, f"金币数量错误: {alice.state.get('gold')}"
        assert result["nodes_executed"] == 1, "应该执行 1 个节点"
        
        # 测试 environment rule  
        env_config = {
            "nodes": [{
                "id": "env_rule_test",
                "selector": {"type": "environment"},
                "operators": [{
                    "type": "rule", 
                    "rule_name": "update_weather_rule",
                    "weather": "rainy"
                }],
                "dependencies": []
            }]
        }
        
        schedule2 = Schedule(env_config, registry)
        
        async def run_env_test():
            return await schedule2.execute_step(world)
        
        result2 = asyncio.run(run_env_test())
        
        # 验证 environment rule 结果
        env = world.get_environment()
        assert env.state["weather"] == "rainy", f"天气更新失败: {env.state.get('weather')}"
        assert env.state["updated_by"] == "rule_operator", "环境更新标记错误"
        
        world.event_logger.close()
        
        print("✅ Rule Operator 测试通过")
        print(f"   Agent 状态: {dict(alice.state)}")
        print(f"   Environment 状态: {dict(env.state)}")
        return True
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_builtin_selectors():
    """测试内置 Selectors"""
    print("=== 测试: 内置 Selectors ===")
    
    import asyncio
    import tempfile
    from simengine.core_data import World
    from simengine.function_registry import FunctionRegistry
    from simengine.schedule import Schedule
    from simengine.context_stack import ContextStack
    
    # 创建临时日志文件
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # 创建 World 和数据
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "llm")
        world.add_agent_data("bob", "teacher", "rule")
        world.add_agent_data("charlie", "student", "llm")
        
        # 创建函数注册表和测试规则
        registry = FunctionRegistry()
        
        @registry.agent.rule("标记规则")
        async def mark_rule(agent, world, params):
            agent.state["selected_by"] = params.get("selector_type", "unknown")
            return f"标记了 {agent.id}"
        
        # 测试 by_type selector
        type_config = {
            "nodes": [{
                "id": "type_selector_test",
                "selector": {
                    "type": "by_type",
                    "agent_type": "student"
                },
                "operators": [{
                    "type": "rule",
                    "rule_name": "mark_rule", 
                    "selector_type": "by_type"
                }],
                "dependencies": []
            }]
        }
        
        schedule1 = Schedule(type_config, registry)
        world.set_context_stack(ContextStack().push_step("step_0"))
        
        async def run_type_test():
            return await schedule1.execute_step(world)
        
        result1 = asyncio.run(run_type_test())
        
        # 验证 by_type 结果 - 只有学生被选中
        alice = world.get_agent("alice")
        bob = world.get_agent("bob")
        charlie = world.get_agent("charlie")
        
        assert alice.state.get("selected_by") == "by_type", "Alice 应该被 by_type 选中"
        assert charlie.state.get("selected_by") == "by_type", "Charlie 应该被 by_type 选中"
        assert bob.state.get("selected_by") != "by_type", "Bob 不应该被 by_type 选中"
        
        # 测试 by_archetype selector
        arch_config = {
            "nodes": [{
                "id": "arch_selector_test",
                "selector": {
                    "type": "by_archetype",
                    "archetype": "llm"
                },
                "operators": [{
                    "type": "rule",
                    "rule_name": "mark_rule",
                    "selector_type": "by_archetype"
                }],
                "dependencies": []
            }]
        }
        
        schedule2 = Schedule(arch_config, registry)
        
        async def run_arch_test():
            return await schedule2.execute_step(world)
        
        result2 = asyncio.run(run_arch_test())
        
        # 验证 by_archetype 结果 - 只有 LLM agents 被选中
        assert alice.state.get("selected_by") == "by_archetype", "Alice 应该被 by_archetype 重新选中"
        assert charlie.state.get("selected_by") == "by_archetype", "Charlie 应该被 by_archetype 重新选中"
        # Bob 状态应该没有变化（仍然不是 by_archetype）
        
        # 测试 by_id selector
        id_config = {
            "nodes": [{
                "id": "id_selector_test",
                "selector": {
                    "type": "by_id",
                    "agent_ids": ["bob"]
                },
                "operators": [{
                    "type": "rule",
                    "rule_name": "mark_rule",
                    "selector_type": "by_id"
                }],
                "dependencies": []
            }]
        }
        
        schedule3 = Schedule(id_config, registry)
        
        async def run_id_test():
            return await schedule3.execute_step(world)
        
        result3 = asyncio.run(run_id_test())
        
        # 验证 by_id 结果 - 只有 Bob 被选中
        assert bob.state.get("selected_by") == "by_id", "Bob 应该被 by_id 选中"
        
        world.event_logger.close()
        
        print("✅ 内置 Selectors 测试通过")
        print(f"   Alice 最终状态: {dict(alice.state)}")
        print(f"   Bob 最终状态: {dict(bob.state)}")
        print(f"   Charlie 最终状态: {dict(charlie.state)}")
        return True
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def main():
    """运行内置 Operators 综合测试"""
    print("🚀 开始内置 Operators 综合测试\n")
    
    tests = [
        test_rule_operator,
        test_action_operator,
        test_builtin_selectors,
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
        print("🎉 内置 Operators 综合测试通过！所有操作器在新架构下正常工作。")
        return True
    else:
        print(f"💥 有 {failed} 个测试失败，需要修复问题。")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
