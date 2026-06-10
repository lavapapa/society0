"""
测试Schedule系统中的behavior operator支持

验证新增的behavior operator是否能正确集成到Schedule V2系统中
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import MagicMock
import asyncio

def test_behavior_registry():
    """测试behavior在FunctionRegistry中的注册"""
    print("🔧 测试behavior注册系统...")

    from simengine.function_registry import FunctionRegistry

    # 创建注册表
    registry = FunctionRegistry()

    # 测试注册behavior
    @registry.sched.behavior("Test behavior for digital trust update")
    async def update_digital_trust(agent, env_proxy, trust_delta=0.1, source="test"):
        """测试behavior函数"""
        # 模拟业务逻辑
        old_trust = agent.state.get("digital_trust", 0.5)
        new_trust = old_trust + trust_delta
        agent.state["digital_trust"] = new_trust

        return {
            "agent_id": agent.id,
            "trust_before": old_trust,
            "trust_after": new_trust,
            "delta_applied": trust_delta,
            "source": source
        }

    # 验证注册成功
    assert "update_digital_trust" in registry.behaviors
    print(f"   ✅ behavior注册成功: update_digital_trust")

    # 验证函数信息
    behavior_info = registry.behaviors["update_digital_trust"]
    assert "function" in behavior_info
    assert "description" in behavior_info
    assert "signature" in behavior_info
    print(f"   ✅ behavior信息完整: {list(behavior_info.keys())}")

    print("✅ behavior注册系统测试通过")

def test_behavior_operator_compilation():
    """测试behavior operator编译"""
    print("⚙️ 测试behavior operator编译...")

    from simengine.schedule import StepFlow
    from simengine.function_registry import FunctionRegistry

    # 创建注册表和StepFlow
    registry = FunctionRegistry()

    # 注册测试behavior
    @registry.sched.behavior("Update agent trust level")
    async def update_agent_trust(agent, env_proxy, delta=0.1):
        agent.state["trust"] = agent.state.get("trust", 0.5) + delta
        return {"new_trust": agent.state["trust"]}

    # 创建一个简单的step配置用于测试
    step_config = {
        "nodes": []  # 空节点配置，我们只需要测试_compile_operator方法
    }

    # 创建StepFlow实例
    step_flow = StepFlow(step_number=0, step_config=step_config, function_registry=registry)

    # 测试behavior operator配置编译
    behavior_config = {
        "type": "behavior",
        "name": "update_agent_trust",
        "delta": 0.2
    }

    try:
        operator_func, params = step_flow._compile_operator(behavior_config)
        print(f"   ✅ behavior operator编译成功")
        print(f"   ✅ 返回参数: {list(params.keys())}")
        assert callable(operator_func)
        assert "delta" in params
        assert params["delta"] == 0.2
    except Exception as e:
        print(f"   ❌ behavior operator编译失败: {e}")
        raise

    print("✅ behavior operator编译测试通过")

def test_behavior_operator_execution():
    """测试behavior operator执行"""
    print("🚀 测试behavior operator执行...")

    from simengine.schedule import StepFlow
    from simengine.function_registry import FunctionRegistry
    from simengine.core_data import BaseOperatorResult, ExecutionContext

    # 创建注册表
    registry = FunctionRegistry()

    # 注册测试behavior
    @registry.sched.behavior("Digital trust calculation")
    async def calculate_trust(agent, env_proxy, base_trust=0.5, adjustment=0.1):
        """计算数字信任的behavior"""
        old_trust = agent.state.get("digital_trust", base_trust)
        new_trust = max(0.0, min(1.0, old_trust + adjustment))  # 限制在0-1之间
        agent.state["digital_trust"] = new_trust

        return {
            "agent_id": agent.id,
            "calculation_type": "digital_trust",
            "previous_trust": old_trust,
            "current_trust": new_trust,
            "adjustment_applied": adjustment,
            "calculation_success": True
        }

    # 创建StepFlow和模拟环境
    step_config = {"nodes": []}
    step_flow = StepFlow(step_number=0, step_config=step_config, function_registry=registry)

    # 创建模拟的agent和context
    mock_agent = MagicMock()
    mock_agent.id = "test_agent_001"
    mock_agent.state = {"digital_trust": 0.6}

    mock_world = MagicMock()
    mock_env = MagicMock()
    mock_world.get_environment.return_value = mock_env

    mock_context = MagicMock()
    mock_context.world = mock_world

    # 编译并执行behavior operator
    behavior_config = {
        "type": "behavior",
        "name": "calculate_trust",
        "base_trust": 0.5,
        "adjustment": 0.15
    }

    async def run_test():
        # 编译operator
        operator_func, params = step_flow._compile_operator(behavior_config)

        # 执行operator
        result = await operator_func([mock_agent], params, mock_context)

        # 验证结果
        assert isinstance(result, BaseOperatorResult)
        assert result.agent_id == "test_agent_001"
        assert result.status == "success"

        # 验证返回的业务数据
        value = result.value
        assert value["agent_id"] == "test_agent_001"
        assert value["calculation_type"] == "digital_trust"
        assert value["previous_trust"] == 0.6
        assert value["current_trust"] == 0.75  # 0.6 + 0.15
        assert value["adjustment_applied"] == 0.15
        assert value["calculation_success"] == True

        # 验证agent状态被更新
        assert mock_agent.state["digital_trust"] == 0.75

        print(f"   ✅ behavior执行成功，agent状态已更新")
        print(f"   ✅ 返回结果: {result.status}, 执行时间: {result.execution_time}")
        print(f"   ✅ 业务数据: trust {value['previous_trust']} -> {value['current_trust']}")

        return True

    # 运行异步测试
    success = asyncio.run(run_test())
    assert success

    print("✅ behavior operator执行测试通过")

def test_behavior_error_handling():
    """测试behavior error处理"""
    print("🛡️ 测试behavior错误处理...")

    from simengine.schedule import StepFlow
    from simengine.function_registry import FunctionRegistry

    registry = FunctionRegistry()

    # 注册会抛出异常的behavior
    @registry.sched.behavior("Failing behavior for testing")
    async def failing_behavior(agent, env_proxy, should_fail=True):
        if should_fail:
            raise ValueError("Intentional test error")
        return {"status": "success"}

    step_config = {"nodes": []}
    step_flow = StepFlow(step_number=0, step_config=step_config, function_registry=registry)

    # 创建模拟环境
    mock_agent = MagicMock()
    mock_agent.id = "error_test_agent"
    mock_agent.state = {}

    mock_context = MagicMock()
    mock_context.world = MagicMock()
    mock_context.world.get_environment.return_value = MagicMock()

    async def run_error_test():
        # 测试不存在的behavior
        try:
            operator_func, params = step_flow._compile_operator({
                "type": "behavior",
                "name": "nonexistent_behavior"
            })
            result = await operator_func([mock_agent], params, mock_context)
            assert result.status == "error"
            assert "not found in registry" in result.error_message
            print("   ✅ 不存在behavior的错误处理正确")
        except Exception as e:
            print(f"   ❌ 错误处理测试失败: {e}")
            raise

        # 测试behavior执行异常
        try:
            operator_func, params = step_flow._compile_operator({
                "type": "behavior",
                "name": "failing_behavior",
                "should_fail": True
            })
            result = await operator_func([mock_agent], params, mock_context)
            assert result.status == "error"
            assert "Intentional test error" in result.error_message
            print("   ✅ behavior执行异常的错误处理正确")
        except Exception as e:
            print(f"   ❌ behavior异常处理测试失败: {e}")
            raise

        return True

    success = asyncio.run(run_error_test())
    assert success

    print("✅ behavior错误处理测试通过")

def main():
    """运行所有behavior operator测试"""
    print("🧪 开始Schedule系统behavior operator集成测试\n")

    try:
        test_behavior_registry()
        print()

        test_behavior_operator_compilation()
        print()

        test_behavior_operator_execution()
        print()

        test_behavior_error_handling()
        print()

        print("🎉 所有behavior operator测试通过！")
        print("📊 新增功能验证：")
        print("   ✅ FunctionRegistry支持behavior注册")
        print("   ✅ Schedule._compile_operator支持behavior类型")
        print("   ✅ _create_behavior_operator工厂方法工作正常")
        print("   ✅ behavior operator支持V2并行Agent模式")
        print("   ✅ 完整的错误处理和异常管理")
        print("   ✅ 灵活的返回值格式支持")
        print()
        print("🎯 Schedule系统现在完全支持behavior operator！")

        return True

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)