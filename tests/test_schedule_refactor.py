#!/usr/bin/env python3
"""
测试 Schedule 架构升级

测试新实现的功能：
1. BaseOperatorResult 数据契约
2. JMESPath 模板系统 + fallback 机制  
3. 新的 converter 类型（jmespath, 更新的 summary）
4. 错误处理和边界情况
"""

import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

async def test_schedule_refactor():
    """测试 Schedule 架构升级"""
    
    print("=== Schedule Architecture Refactor Test ===")
    
    # 1. 初始化全局Milvus
    print("\n1. 初始化全局Milvus...")
    from simengine.agent import init_global_milvus
    
    try:
        await init_global_milvus("./test_schedule_refactor_milvus.db")
        print("✅ Milvus全局实例初始化成功")
    except Exception as e:
        print(f"❌ Milvus初始化失败: {e}")
        return False
    
    # 2. 创建模拟函数
    print("\n2. 设置模拟函数...")
    
    async def mock_llm_call(payload: dict) -> dict:
        """模拟LLM调用"""
        return {"role": "assistant", "content": "模拟LLM响应：任务已完成"}
    
    async def mock_embed_call(texts: list, dimensions: int = 512) -> dict:
        """模拟embedding函数"""
        import random
        embeddings = []
        for text in texts:
            embedding = [random.uniform(-1.0, 1.0) for _ in range(dimensions)]
            embeddings.append(embedding)
        
        return {"result": embeddings, "model": "mock-embed-model", "dimensions": dimensions}
    
    print("✅ 模拟函数设置完成")
    
    # 3. 测试 BaseOperatorResult 和 InstructOperatorResult
    print("\n3. 测试 BaseOperatorResult 数据契约...")
    from simengine.core_data import BaseOperatorResult, InstructOperatorResult
    
    try:
        # 测试基础 BaseOperatorResult
        base_result = BaseOperatorResult(
            agent_id="test_agent",
            status="success",
            value={"some": "data"},
            execution_time=0.123,
            metadata={"test": True}
        )
        
        print("✅ BaseOperatorResult 创建成功")
        print(f"   agent_id: {base_result.agent_id}")
        print(f"   status: {base_result.status}")
        print(f"   value: {base_result.value}")
        
        # 测试 InstructOperatorResult
        instruct_result = InstructOperatorResult(
            agent_id="llm_agent",
            status="success",
            performative_output="测试完成",
            structured_output={"result": "success"},
            total_turns=3,
            fovs_used=["test_fov"]
        )
        
        print("✅ InstructOperatorResult 创建成功")
        print(f"   继承自 BaseOperatorResult: {isinstance(instruct_result, BaseOperatorResult)}")
        print(f"   value 自动设置: {instruct_result.value is not None}")
        
    except Exception as e:
        print(f"❌ BaseOperatorResult 测试失败: {e}")
        return False
    
    # 4. 测试 JMESPath 模板系统
    print("\n4. 测试 JMESPath 模板系统...")
    from simengine.schedule import StepFlow
    from simengine.function_registry import FunctionRegistry
    from simengine.core_data import WorldState, Environment
    
    try:
        # 创建测试环境
        registry = FunctionRegistry()
        world = WorldState(step=5, environment=Environment(type="test"))
        world.globals = {"market_price": 100, "weather": "sunny"}
        
        # 创建 StepFlow 来测试模板渲染
        step_flow = StepFlow(0, {"nodes": []}, registry)
        step_flow.step_context = {
            "market_analysis": {"trend": "up", "confidence": 0.8},
            "weather_data": {"temperature": 25, "humidity": 0.6}
        }
        
        # 测试不同的模板格式
        test_params = {
            # JMESPath 表达式
            "jmespath_simple": "globals.market_price",
            "jmespath_complex": "context.market_analysis.confidence",
            "jmespath_array": "[step, globals.market_price]",
            
            # Legacy 模板（应该 fallback）
            "legacy_template": "{step}",
            "legacy_complex": "{globals.market_price}",
            
            # 普通字符串（应该保持不变）
            "plain_string": "just a string",
            "number": 42
        }
        
        rendered = step_flow._render_template(test_params, world, {"test_input": "value"})
        
        print("✅ JMESPath 模板系统测试成功")
        print(f"   JMESPath simple: {rendered['jmespath_simple']} (期望: 100)")
        print(f"   JMESPath complex: {rendered['jmespath_complex']} (期望: 0.8)")
        print(f"   JMESPath array: {rendered['jmespath_array']} (期望: [5, 100])")
        print(f"   Legacy template: {rendered['legacy_template']} (期望: 5)")
        print(f"   Plain string: {rendered['plain_string']} (期望: just a string)")
        print(f"   Number: {rendered['number']} (期望: 42)")
        
        # 验证结果
        assert rendered['jmespath_simple'] == 100
        assert rendered['jmespath_complex'] == 0.8
        assert rendered['jmespath_array'] == [5, 100]
        assert rendered['legacy_template'] == "5"
        assert rendered['plain_string'] == "just a string"
        assert rendered['number'] == 42
        
        print("   ✅ 所有模板渲染结果正确")
        
    except Exception as e:
        print(f"❌ JMESPath 模板系统测试失败: {e}")
        return False
    
    # 5. 测试新的 converter 系统
    print("\n5. 测试新的 converter 系统...")
    from simengine.core_data import ExecutionContext
    
    try:
        # 创建测试 BaseOperatorResult 对象
        test_results = [
            BaseOperatorResult(
                agent_id="agent1", 
                status="success", 
                value={"score": 95}, 
                execution_time=0.1,
                metadata={"type": "test"}
            ),
            BaseOperatorResult(
                agent_id="agent2", 
                status="error", 
                value=None, 
                error_message="Test error",
                execution_time=0.2
            ),
            BaseOperatorResult(
                agent_id="agent3", 
                status="success", 
                value={"score": 87}, 
                execution_time=0.15
            )
        ]
        
        # 创建执行上下文
        context = ExecutionContext(world=world, step=None, node=None, caller="test")
        
        # 测试 JMESPath converter
        jmespath_result = await step_flow._jmespath_converter(
            test_results, 
            {"expression": "[?status=='success'].value.score"}, 
            context
        )
        
        print("✅ JMESPath converter 测试成功")
        print(f"   表达式: [?status=='success'].value.score")
        print(f"   结果: {jmespath_result['jmespath_result']} (期望: [95, 87])")
        assert jmespath_result['jmespath_result'] == [95, 87]
        
        # 测试 summary converter
        summary_result = await step_flow._summary_converter(test_results, {}, context)
        
        print("✅ Summary converter 测试成功") 
        print(f"   总数: {summary_result['total_operators']} (期望: 3)")
        print(f"   成功数: {summary_result['success_count']} (期望: 2)")
        print(f"   错误数: {summary_result['error_count']} (期望: 1)")
        print(f"   成功率: {summary_result['success_rate']} (期望: 0.67)")
        assert summary_result['total_operators'] == 3
        assert summary_result['success_count'] == 2
        assert summary_result['error_count'] == 1
        assert abs(summary_result['success_rate'] - 0.6666666666666666) < 0.001
        
        # 测试 passthrough converter
        passthrough_result = await step_flow._passthrough_converter(test_results, {}, context)
        
        print("✅ Passthrough converter 测试成功")
        print(f"   结果数量: {passthrough_result['count']} (期望: 3)")
        print(f"   包含完整数据: {len(passthrough_result['operator_results'])} (期望: 3)")
        assert passthrough_result['count'] == 3
        assert len(passthrough_result['operator_results']) == 3
        
    except Exception as e:
        print(f"❌ Converter 系统测试失败: {e}")
        return False
    
    # 6. 测试错误处理
    print("\n6. 测试错误处理...")
    try:
        # 测试无效的 JMESPath 表达式
        invalid_jmespath_result = await step_flow._jmespath_converter(
            test_results,
            {"expression": "invalid..expression[["},
            context
        )
        
        print("✅ JMESPath 错误处理测试成功")
        print(f"   错误信息: {invalid_jmespath_result.get('jmespath_error', 'None')}")
        assert 'jmespath_error' in invalid_jmespath_result
        
        # 测试空的 operator_results
        empty_summary = await step_flow._summary_converter([], {}, context)
        print("✅ 空结果处理测试成功")
        print(f"   空结果成功率: {empty_summary['success_rate']} (期望: 0)")
        assert empty_summary['success_rate'] == 0
        
    except Exception as e:
        print(f"❌ 错误处理测试失败: {e}")
        return False
    
    print(f"\n🎉 Schedule 架构升级测试全部通过！")
    print("✅ BaseOperatorResult 数据契约正常工作")
    print("✅ JMESPath 模板系统 + fallback 机制正常工作")
    print("✅ 新的 converter 系统正常工作")
    print("✅ 错误处理机制正常工作")
    print("✅ 向后兼容性保持")
    
    return True

if __name__ == "__main__":
    asyncio.run(test_schedule_refactor())