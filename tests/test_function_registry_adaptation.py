#!/usr/bin/env python3
"""
Function Registry 适配测试

测试 FunctionRegistry 是否正确适配新的统一状态架构：
1. 验证注册接口保持兼容
2. 测试新的函数签名文档
3. 确保旧的注册方式仍然工作
4. 验证与 Schedule 的集成
"""

import sys
import os
from typing import Any, Dict
from types import ModuleType

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

def test_function_registry_compatibility():
    """测试 FunctionRegistry 接口兼容性"""
    print("=== 测试: FunctionRegistry 接口兼容性 ===")
    
    from simengine.function_registry import FunctionRegistry
    
    # 创建注册表
    registry = FunctionRegistry()
    
    # 测试所有注册接口是否可用
    assert hasattr(registry, 'env'), "缺少 env 属性"
    assert hasattr(registry, 'agent'), "缺少 agent 属性"
    assert hasattr(registry, 'sched'), "缺少 sched 属性"
    
    # 测试环境函数注册
    assert hasattr(registry.env, 'fov'), "缺少 env.fov 方法"
    assert hasattr(registry.env, 'rule'), "缺少 env.rule 方法"
    assert hasattr(registry.env, 'empower'), "缺少 env.empower 方法"
    
    # 测试 Agent 函数注册
    assert hasattr(registry.agent, 'rule'), "缺少 agent.rule 方法"
    assert hasattr(registry.agent, 'action'), "缺少 agent.action 方法"
    
    # 测试 Schedule 函数注册
    assert hasattr(registry.sched, 'selector'), "缺少 sched.selector 方法"
    assert hasattr(registry.sched, 'operator'), "缺少 sched.operator 方法"
    assert hasattr(registry.sched, 'converter'), "缺少 sched.converter 方法"
    
    # 验证内部字典存在
    assert hasattr(registry, 'env_fovs'), "缺少 env_fovs 字典"
    assert hasattr(registry, 'env_rules'), "缺少 env_rules 字典"
    assert hasattr(registry, 'env_empowers'), "缺少 env_empowers 字典"
    assert hasattr(registry, 'agent_rules'), "缺少 agent_rules 字典"
    assert hasattr(registry, 'agent_actions'), "缺少 agent_actions 字典"
    assert hasattr(registry, 'selectors'), "缺少 selectors 字典"
    assert hasattr(registry, 'operators'), "缺少 operators 字典"
    assert hasattr(registry, 'converters'), "缺少 converters 字典"
    
    print("✅ FunctionRegistry 接口兼容性测试通过")
    return True


def test_function_registration():
    """测试函数注册功能"""
    print("=== 测试: 函数注册功能 ===")
    
    from simengine.function_registry import FunctionRegistry
    
    registry = FunctionRegistry()
    
    # 测试环境 FoV 注册
    @registry.env.fov("测试视野函数")
    def test_fov(agent, env):
        return f"Agent {agent.id} sees environment {env.type}"
    
    assert "test_fov" in registry.env_fovs, "FoV 函数未正确注册"
    assert "env.test_fov" in registry.env_fovs, "FoV 函数未写入 env.<name> 短 ID"
    assert registry.env_fovs["test_fov"]["description"] == "测试视野函数", "FoV 函数描述错误"
    
    # 测试环境规则注册
    @registry.env.rule("测试环境规则")  
    async def test_env_rule(environment, world, params):
        return "环境规则执行"
    
    assert "test_env_rule" in registry.env_rules, "环境规则未正确注册"
    assert "env.test_env_rule" in registry.env_rules, "环境规则未写入 env.<name> 短 ID"
    assert "env.test_env_rule" in registry.rules, "环境规则未同步至统一 rules 字典"
    
    # 测试 Agent 规则注册
    @registry.agent.rule("测试 Agent 规则")
    async def test_agent_rule(agent, world, params):
        return "Agent 规则执行"
    
    assert "test_agent_rule" in registry.agent_rules, "Agent 规则未正确注册"
    
    # 测试 Agent 动作注册
    @registry.agent.action("测试 Agent 动作")
    async def test_agent_action(agent_ids, world, params):
        return "Agent 动作执行"
    
    assert "test_agent_action" in registry.agent_actions, "Agent 动作未正确注册"
    
    # 测试自定义 selector 注册
    @registry.sched.selector("测试选择器")
    async def test_selector(params, context):
        return []
    
    assert "test_selector" in registry.selectors, "选择器未正确注册"
    
    # 测试自定义 operator 注册
    @registry.sched.operator("测试操作器")
    async def test_operator(agents, params, context):
        from simengine.core_data import BaseOperatorResult
        return BaseOperatorResult(agent_id="test", status="success", value="操作完成")
    
    assert "test_operator" in registry.operators, "操作器未正确注册"
    
    print("✅ 函数注册功能测试通过")
    print(f"   注册的 FoV 函数: {len(registry.env_fovs)}")
    print(f"   注册的环境规则: {len(registry.env_rules)}")
    print(f"   注册的 Agent 规则: {len(registry.agent_rules)}")
    print(f"   注册的 Agent 动作: {len(registry.agent_actions)}")
    print(f"   注册的选择器: {len(registry.selectors)}")
    print(f"   注册的操作器: {len(registry.operators)}")
    return True


def test_registry_with_schedule():
    """测试 FunctionRegistry 与 Schedule 的集成"""
    print("=== 测试: FunctionRegistry 与 Schedule 集成 ===")
    
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
        # 创建 World 和基础数据
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "rule")
        
        # 创建函数注册表和注册新的环境规则
        registry = FunctionRegistry()

        class _DummyEnvironment:
            def __init__(self, world):
                self.world = world
                self.state: Dict[str, Any] = {}

        # 提前注入简易环境，避免 World 在 schedule 运行时尝试自动实例化真实环境
        world._environment_cache = _DummyEnvironment(world)  # type: ignore[attr-defined]

        @registry.env.rule("新架构测试规则")
        async def new_arch_rule(env, message: str):
            """环境规则：为测试代理写入状态"""
            for agent_id in list(world.agents_data.keys()):
                agent = world.get_agent(agent_id)
                agent.state["new_arch_test"] = True
                agent.state["test_message"] = message
            return message
        
        # 创建 Schedule 配置
        schedule_config = {
            "nodes": [
                {
                    "id": "new_arch_test_node",
                    "selector": {
                        "type": "all_agents"
                    },
                        "operators": [
                            {
                                "type": "rule",
                                "name": "env.new_arch_rule",
                                "message": "FunctionRegistry 适配成功"
                            }
                        ],
                    "dependencies": []
                }
            ]
        }
        
        # 创建 Schedule
        schedule = Schedule(schedule_config, registry)
        
        # 设置上下文栈
        initial_stack = ContextStack().push_step("step_0")
        world.set_context_stack(initial_stack)
        
        # 执行测试
        async def run_test():
            result = await schedule.execute_step(world)
            return result
        
        result = asyncio.run(run_test())
        
        # 验证结果
        assert result["nodes_executed"] == 1, f"应该执行 1 个节点，实际 {result['nodes_executed']}"
        
        # 验证状态修改
        alice = world.get_agent("alice")
        assert alice.state["new_arch_test"] == True, "新架构标记未设置"
        assert alice.state["test_message"] == "FunctionRegistry 适配成功", f"测试消息错误: {alice.state['test_message']}"
        
        # 关闭事件记录器
        world.event_logger.close()
        
        print("✅ FunctionRegistry 与 Schedule 集成测试通过")
        print(f"   执行结果: {result['nodes_executed']} 个节点")
        print(f"   Alice 状态: {dict(alice.state)}")
        return True

    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_register_logic_module_without_module_cache():
    """当模块未注册进 sys.modules 时，register_logic_module 也应正确收集 LogicMeta。"""
    from simengine.decorators import logic
    from simengine.function_registry import FunctionRegistry, register_logic_module

    module_name = "temp_logic_module_for_registry"
    if module_name in sys.modules:
        sys.modules.pop(module_name)

    module = ModuleType(module_name)
    module.logic = logic

    exec(
        """
@logic.behavior(name="测试行为")
async def sample_behavior(agent, env, **kwargs):
    return {"status": "ok"}


@logic.rule(name="测试规则")
async def sample_rule(env, **kwargs):
    return {"status": "ok"}


@logic.selector(name="测试选择器")
async def sample_selector(agents, env, flag: bool = True):
    return agents if flag else []
""",
        module.__dict__,
    )

    registry = FunctionRegistry()
    count = register_logic_module(registry, module)

    assert count == 3, f"应注册 3 个 Logic 函数，实际 {count}"
    assert "temp_logic_module_for_registry.sample_behavior" in registry.behaviors
    assert "temp_logic_module_for_registry.sample_rule" in registry.rules
    assert "temp_logic_module_for_registry.sample_selector" in registry.selectors


def test_schedule_with_custom_selector():
    """验证通过 @logic.selector 注册的自定义选择器可在 Schedule 中使用。"""
    print("=== 测试: 自定义 selector 与 Schedule 集成 ===")

    import asyncio
    import tempfile
    from simengine.core_data import World
    from simengine.function_registry import FunctionRegistry, register_logic_module
    from simengine.schedule import Schedule
    from simengine.context_stack import ContextStack
    from simengine.decorators import logic

    module_name = "temp_selector_module"
    module = ModuleType(module_name)
    module.logic = logic
    sys.modules[module_name] = module

    log_path = None
    world = None

    exec(
        """
@logic.selector(name="按 ID 过滤")
async def select_by_id(agents, env, target_ids: list[str]):
    return [agent for agent in agents if agent.id in target_ids]


@logic.behavior(name="标记被选中的 Agent")
async def mark_selected(agent, env, flag: str = "selected"):
    agent.state[flag] = True
    return {"agent_id": agent.id, "flag": flag}
""",
        module.__dict__,
    )

    try:
        registry = FunctionRegistry()
        register_logic_module(registry, module)

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".jsonl") as f:
            log_path = f.name

        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "llm")
        world.add_agent_data("bob", "student", "llm")
        world.agents_data["alice"]["state"]["active"] = True
        world.agents_data["bob"]["state"]["active"] = False

        class _DummyEnvironment:
            def __init__(self, world):
                self.world = world
                self.state: Dict[str, Any] = {}

        world._environment_cache = _DummyEnvironment(world)  # type: ignore[attr-defined]

        schedule_config = {
            "nodes": [
                {
                    "id": "custom_selector_node",
                    "selector": {
                        "type": "custom",
                        "function": f"{module_name}.select_by_id",
                        "target_ids": ["alice"],
                    },
                    "operators": [
                        {
                            "id": "mark_selected_operator",
                            "type": "behavior",
                            "name": f"{module_name}.mark_selected",
                            "flag": "selected_via_custom",
                        }
                    ],
                }
            ]
        }

        schedule = Schedule(schedule_config, registry)
        initial_stack = ContextStack().push_step("step_0")
        world.set_context_stack(initial_stack)

        async def run_schedule():
            return await schedule.execute_step(world)

        result = asyncio.run(run_schedule())
        assert result["nodes_executed"] == 1, "应该执行 1 个节点"

        alice = world.get_agent("alice")
        bob = world.get_agent("bob")

        assert alice.state.get("selected_via_custom") is True, "Alice 应被标记为选中"
        assert bob.state.get("selected_via_custom") is None, "Bob 不应被标记为选中"

        print("✅ 自定义 selector 测试通过")
        return True
    finally:
        sys.modules.pop(module_name, None)
        if world is not None:
            world.close()
        if log_path and os.path.exists(log_path):
            os.unlink(log_path)


def main():
    """运行 FunctionRegistry 适配测试"""
    print("🚀 开始 FunctionRegistry 适配测试\n")
    
    tests = [
        test_function_registry_compatibility,
        test_function_registration,
        test_registry_with_schedule,
        test_register_logic_module_without_module_cache,
        test_schedule_with_custom_selector,
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
        print("🎉 FunctionRegistry 适配测试通过！现有功能保持兼容，新架构完全集成。")
        return True
    else:
        print(f"💥 有 {failed} 个测试失败，需要修复问题。")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
