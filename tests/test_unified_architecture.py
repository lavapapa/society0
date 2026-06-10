#!/usr/bin/env python3
"""
统一状态架构核心组件测试

测试从最小的组件开始，逐步验证：
1. DictProxy 和 ListProxy 基础功能
2. ContextStack 不可变栈操作
3. 事件系统和 Transaction 机制
4. World、Agent、Environment 集成
5. 端到端的状态修改和事件记录

这个测试是一个渐进式的验证过程，每一步失败都能精确定位问题。
"""

import sys
import os
import asyncio
import tempfile
import json
from pathlib import Path

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

def test_01_dict_proxy_basic():
    """测试 DictProxy 基础功能"""
    print("=== 测试 1: DictProxy 基础功能 ===")
    
    from simengine.state_proxy import DictProxy
    
    # 创建测试数据和记录器
    test_data = {"name": "Alice", "age": 25, "skills": ["python", "ai"]}
    events = []
    
    def mock_recorder(event):
        events.append(event)
    
    def mock_context():
        return [{"type": "test", "id": "test_1"}]
    
    # 创建代理
    proxy = DictProxy(test_data, mock_recorder, mock_context, ("test", "data"))
    
    # 测试基础操作
    proxy.name = "Bob"  # 属性修改
    proxy.location = "NYC"  # 属性新增
    del proxy.age  # 属性删除

    # 验证属性访问
    assert proxy.name == "Bob"
    assert getattr(proxy, "location") == "NYC"
    assert hasattr(proxy, "skills"), "skills 应该存在"
    dir_entries = dir(proxy)
    assert "name" in dir_entries and "location" in dir_entries

    # 验证代理操作
    assert test_data["name"] == "Bob", f"期望 'Bob'，实际 '{test_data['name']}'"
    assert test_data["location"] == "NYC", f"期望 'NYC'，实际 '{test_data['location']}'"
    assert "age" not in test_data, "age 应该被删除"
    
    # 验证事件记录
    assert len(events) == 3, f"期望 3 个事件，实际 {len(events)}"
    
    print("✅ DictProxy 基础功能正常")
    print(f"   记录了 {len(events)} 个事件")
    print(f"   最终数据: {test_data}")
    return True


def test_02_context_stack():
    """测试 ContextStack 不可变栈"""
    print("\n=== 测试 2: ContextStack 不可变栈 ===")
    
    from simengine.context_stack import ContextStack, ContextFrame
    
    # 创建空栈
    stack = ContextStack()
    assert stack.is_empty(), "新栈应该为空"
    
    # 添加帧
    step_frame = ContextFrame("step", "step_1", {"step_num": 1})
    stack2 = stack.push(step_frame)
    
    # 验证不可变性
    assert stack.is_empty(), "原栈应该保持空"
    assert stack2.size() == 1, "新栈应该有 1 个帧"
    
    # 继续添加
    node_frame = ContextFrame("node", "node_a", {"node_type": "test"})
    stack3 = stack2.push(node_frame)
    
    assert stack2.size() == 1, "中间栈应该保持 1 个帧"
    assert stack3.size() == 2, "最新栈应该有 2 个帧"
    
    # 测试便捷方法
    stack4 = stack3.push_operator("test_op", {"param": "value"})
    assert stack4.size() == 3, "应该有 3 个帧"
    assert stack4.get_current_operator() == "test_op", "当前操作器应该是 test_op"
    
    print("✅ ContextStack 不可变栈正常")
    print(f"   最终栈深度: {stack4.size()}")
    print(f"   栈内容: {stack4}")
    return True


def test_03_events_and_transaction():
    """测试事件系统和事务机制"""
    print("\n=== 测试 3: 事件系统和事务机制 ===")
    
    from simengine.events import StateChangeEvent, create_state_change_event
    from simengine.transaction import EventLogger, NodeTransaction, TransactionManager
    from simengine.context_stack import ContextStack
    import tempfile
    
    # 创建临时日志文件
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # 测试事件创建
        context = [{"type": "step", "id": "step_1"}, {"type": "node", "id": "node_a"}]
        event = create_state_change_event(
            target_type="agent",
            target_id="alice", 
            path=["state", "money"],
            operation="set",
            value=100,
            context_stack=context
        )
        
        assert event.target_type == "agent", "目标类型错误"
        assert event.get_current_step() == "step_1", "步骤解析错误"
        assert event.get_current_node() == "node_a", "节点解析错误"
        
        # 测试事务机制
        event_logger = EventLogger(log_path)
        transaction_manager = TransactionManager(event_logger)
        context_stack = ContextStack().push_step("step_1").push_node("node_a")
        
        # 使用事务上下文
        with transaction_manager.transaction("step_1", "node_a", context_stack) as tx:
            tx.record_state_change("agent", "alice", ["state", "money"], "set", 100)
            tx.record_state_change("agent", "alice", ["state", "location"], "set", "NYC")
        
        event_logger.close()
        
        # 验证日志文件
        with open(log_path, 'r') as f:
            lines = f.readlines()
        
        assert len(lines) >= 3, f"期望至少 3 行日志，实际 {len(lines)}"  # start + 2 changes + completion
        
        # 解析第一个状态变更事件
        event_line = None
        for line in lines:
            data = json.loads(line)
            if data.get("event_type") == "STATE_CHANGE":
                event_line = data
                break
        
        assert event_line is not None, "没有找到状态变更事件"
        assert event_line["target_type"] == "agent", "目标类型错误"
        assert event_line["target_id"] == "alice", "目标ID错误"
        
        print("✅ 事件系统和事务机制正常")
        print(f"   记录了 {len(lines)} 行日志")
        return True
        
    finally:
        # 清理临时文件
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_04_world_agent_integration():
    """测试 World、Agent、Environment 集成"""
    print("\n=== 测试 4: World、Agent、Environment 集成 ===")
    
    from simengine.core_data import World
    import tempfile
    
    # 创建临时日志文件
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # 创建 World
        world = World(step=0, event_log_path=log_path)
        
        # 添加 Agent 数据
        world.add_agent_data("alice", "student", "llm")
        world.add_agent_data("bob", "teacher", "rule")
        
        # 获取 Agent 对象
        alice = world.get_agent("alice")
        bob = world.get_agent("bob")
        
        # 验证基本属性
        assert alice.id == "alice", "Agent ID 错误"
        assert alice.type == "student", "Agent 类型错误"
        assert alice.archetype == "llm", "Agent 架构错误"
        
        # 测试状态代理
        alice_state = alice.state
        assert hasattr(alice_state, '__setitem__'), "状态应该是代理对象"
        
        # 获取 Environment 对象
        env = world.get_environment()
        env_state = env.state
        assert hasattr(env_state, '__setitem__'), "环境状态应该是代理对象"
        
        # 测试依赖倒置
        agents_by_type = env.get_agents_by_type("student")
        assert len(agents_by_type) == 1, "应该找到 1 个学生"
        assert agents_by_type[0].id == "alice", "应该是 alice"
        
        print("✅ World、Agent、Environment 集成正常")
        print(f"   创建了 {len(world.agents_data)} 个 Agent")
        print(f"   World 状态摘要: {world.get_state_summary()}")
        return True
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_05_end_to_end_state_changes():
    """测试端到端的状态修改和事件记录"""
    print("\n=== 测试 5: 端到端状态修改和事件记录 ===")
    
    from simengine.core_data import World
    from simengine.context_stack import ContextStack
    import tempfile
    import json
    
    # 创建临时日志文件
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # 创建 World 和设置上下文
        world = World(step=1, event_log_path=log_path)
        context_stack = ContextStack().push_step("step_1").push_node("test_node").push_operator("test_op")
        world.set_context_stack(context_stack)
        
        # 添加测试数据
        world.add_agent_data("alice", "student", "llm")
        
        # 开始事务
        with world.transaction_manager.transaction("step_1", "test_node", context_stack) as tx:
            # 获取代理并修改状态
            alice = world.get_agent("alice")
            env = world.get_environment()
            
            # 测试 Agent 状态修改
            alice.state["money"] = 100
            alice.state["location"] = "library"
            alice.state["skills"] = ["python", "ai"]
            
            # 测试嵌套修改
            alice.state["inventory"] = {"books": 5, "laptop": 1}
            alice.state["inventory"]["books"] += 2  # 嵌套修改
            
            # 测试 Environment 状态修改
            env.state["weather"] = "sunny"
            env.state["time"] = "morning"
            
            # 测试 Agent 属性修改
            alice.properties["student_id"] = "12345"
            alice.properties["gpa"] = 3.8
        
        world.event_logger.close()
        
        # 验证最终状态
        alice_data = world.agents_data["alice"]
        assert alice_data["state"]["money"] == 100, "money 状态错误"
        assert alice_data["state"]["inventory"]["books"] == 7, "嵌套修改失败"
        assert alice_data["properties"]["gpa"] == 3.8, "属性修改失败"
        
        env_data = world.environment_data
        assert env_data["state"]["weather"] == "sunny", "环境状态修改失败"
        
        # 验证事件日志
        with open(log_path, 'r') as f:
            lines = f.readlines()
        
        state_change_events = []
        for line in lines:
            data = json.loads(line)
            if data.get("event_type") == "STATE_CHANGE":
                state_change_events.append(data)
        
        assert len(state_change_events) >= 7, f"期望至少 7 个状态变更事件，实际 {len(state_change_events)}"
        
        # 验证事件上下文
        first_event = state_change_events[0]
        context = first_event["context_stack"]
        assert len(context) == 3, "上下文栈深度错误"
        assert context[0]["type"] == "step", "上下文层级错误"
        assert context[1]["type"] == "node", "上下文层级错误"
        assert context[2]["type"] == "operator", "上下文层级错误"
        
        print("✅ 端到端状态修改和事件记录正常")
        print(f"   修改了 {len(state_change_events)} 个状态")
        print(f"   最终 alice 状态: {alice_data['state']}")
        print(f"   最终环境状态: {env_data['state']}")
        return True
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def main():
    """运行所有测试"""
    print("🚀 开始统一状态架构核心组件测试\n")
    
    tests = [
        test_01_dict_proxy_basic,
        test_02_context_stack,
        test_03_events_and_transaction,
        test_04_world_agent_integration,
        test_05_end_to_end_state_changes,
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
        print("🎉 所有核心组件测试通过！统一状态架构基础功能正常。")
        return True
    else:
        print(f"💥 有 {failed} 个测试失败，需要修复问题。")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
