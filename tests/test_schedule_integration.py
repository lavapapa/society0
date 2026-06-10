#!/usr/bin/env python3
"""
Schedule 集成统一状态架构测试

测试 Schedule 和 StepFlow 是否正确集成了新的统一状态架构：
1. 事务机制集成
2. 上下文栈管理
3. Agent/Environment 代理访问
4. 事件记录
"""

import sys
import os
import asyncio
import tempfile
import json
from pathlib import Path

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

def test_basic_schedule_with_transaction():
    """测试基础的 Schedule 执行和事务集成"""
    print("=== 测试: Schedule 事务集成 ===")
    
    from simengine.core_data import World
    from simengine.function_registry import FunctionRegistry
    from simengine.schedule import Schedule
    from simengine.context_stack import ContextStack
    import tempfile
    
    # 创建临时日志文件
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # 创建 World 和基础数据
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "rule")
        world.add_agent_data("bob", "teacher", "rule")
        
        # 创建函数注册表
        registry = FunctionRegistry()
        
        # 注册一个简单的规则函数
        @registry.agent.rule("测试规则")
        async def test_rule(agent, world, params):
            """简单的测试规则"""
            # 修改 agent 状态
            agent.state["test_value"] = params.get("value", 42)
            agent.state["rule_executed"] = True
            return f"Rule executed for {agent.id}"
        
        # 创建简单的 Schedule 配置
        schedule_config = {
            "nodes": [
                {
                    "id": "test_node",
                    "selector": {
                        "type": "all_agents"
                    },
                    "operators": [
                        {
                            "type": "rule",
                            "rule_name": "test_rule",
                            "value": 100
                        }
                    ],
                    "dependencies": []
                }
            ]
        }
        
        # 创建 Schedule
        schedule = Schedule(schedule_config, registry)
        
        # 调试 Schedule 状态
        print(f"Schedule step_flows: {len(schedule.step_flows)}")
        if schedule.step_flows:
            step_flow = schedule.step_flows[0]
            print(f"StepFlow step_nodes: {len(step_flow.step_nodes)}")
            for node in step_flow.step_nodes:
                print(f"  Node: {node.id}, operators: {len(node.operator_funcs)}")
        
        # 设置初始上下文栈
        initial_stack = ContextStack().push_step("step_0")
        world.set_context_stack(initial_stack)
        
        # 执行一步
        async def run_test():
            result = await schedule.execute_step(world)
            return result
        
        # 运行测试
        result = asyncio.run(run_test())
        
        # 调试输出
        print(f"执行结果: {result}")
        print(f"注册的 agent rules: {list(registry.agent_rules.keys())}")
        
        # 检查 alice 的状态
        alice = world.get_agent("alice")
        print(f"Alice 原始状态: {dict(alice.state)}")
        
        # 验证结果
        assert "nodes_executed" in result, "应该返回执行结果"
        
        # 验证状态修改
        alice = world.get_agent("alice")
        bob = world.get_agent("bob")
        
        assert alice.state["test_value"] == 100, f"Alice 状态错误: {alice.state}"
        assert alice.state["rule_executed"] == True, "Alice 规则执行标记错误"
        assert bob.state["test_value"] == 100, f"Bob 状态错误: {bob.state}"
        assert bob.state["rule_executed"] == True, "Bob 规则执行标记错误"
        
        # 关闭事件记录器
        world.event_logger.close()
        
        # 验证事件记录
        with open(log_path, 'r') as f:
            lines = f.readlines()
        
        state_change_events = []
        for line in lines:
            data = json.loads(line)
            if data.get("event_type") == "STATE_CHANGE":
                state_change_events.append(data)
        
        # 应该至少有 4 个状态变更（每个 agent 2 个状态字段）
        assert len(state_change_events) >= 4, f"状态变更事件数量不足: {len(state_change_events)}"
        
        # 验证事件上下文包含正确的执行路径
        first_event = state_change_events[0]
        context = first_event["context_stack"]
        assert len(context) >= 2, "上下文栈深度不足"
        assert any(frame["type"] == "step" for frame in context), "缺少 step 上下文"
        assert any(frame["type"] == "node" for frame in context), "缺少 node 上下文"
        
        print("✅ Schedule 事务集成测试通过")
        print(f"   执行了 {result.get('nodes_executed', 0)} 个节点")
        print(f"   记录了 {len(state_change_events)} 个状态变更事件")
        print(f"   Alice 最终状态: {dict(alice.state)}")
        print(f"   Bob 最终状态: {dict(bob.state)}")
        return True
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def main():
    """运行 Schedule 集成测试"""
    print("🚀 开始 Schedule 集成统一状态架构测试\n")
    
    tests = [
        test_basic_schedule_with_transaction,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
                print(f"❌ {test.__name__} 测试失败")
        except Exception as e:
            failed += 1
            print(f"❌ {test.__name__} 测试异常: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n📊 测试结果：通过 {passed}/{len(tests)} 个测试")
    
    if failed == 0:
        print("🎉 Schedule 集成测试通过！事务机制正常工作。")
        return True
    else:
        print(f"💥 有 {failed} 个测试失败，需要修复问题。")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)