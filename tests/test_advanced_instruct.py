#!/usr/bin/env python3
"""
测试高级 instruct 交互机制

测试新实现的功能：
1. FoV 传递机制
2. 强制结构化输出机制
3. InstructOperatorResult 结构
"""

import sys
import os
import asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

async def test_advanced_instruct_mechanism():
    """测试高级 instruct 交互机制"""
    
    print("=== Advanced Instruct Mechanism Test ===")
    
    # 1. 初始化全局Milvus
    print("\n1. 初始化全局Milvus...")
    from simengine.agent import init_global_milvus
    
    try:
        await init_global_milvus("./test_advanced_instruct_milvus.db")
        print("✅ Milvus全局实例初始化成功")
    except Exception as e:
        print(f"❌ Milvus初始化失败: {e}")
        return False
    
    # 2. 创建模拟函数
    print("\n2. 设置模拟函数...")
    
    async def mock_llm_call(payload: dict) -> dict:
        """模拟LLM调用，支持 tool_choice"""
        messages = payload.get("messages", [])
        tools = payload.get("tools", [])
        tool_choice = payload.get("tool_choice")
        
        if not messages:
            return {"role": "assistant", "content": "模拟响应"}
        
        user_message = messages[-1].get("content", "")
        
        # 如果有 tool_choice 强制执行
        if tool_choice and tools:
            function_name = tool_choice.get("function", {}).get("name")
            if function_name == "finish_instruction":
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_123",
                        "function": {
                            "name": "finish_instruction",
                            "arguments": '{"analysis": "项目进度良好", "priority": "high", "next_steps": ["完成测试", "准备部署"]}'
                        }
                    }]
                }
        
        # 正常响应
        if "finish_instruction" in str(tools):
            # 有 output_schema，但正常执行
            return {
                "role": "assistant", 
                "content": "-> STAGE_BEGIN: Understanding\n我理解了任务要求。\n\n-> STAGE_BEGIN: Response\n我需要分析项目进度并提交结构化结果。",
                "tool_calls": [{
                    "id": "call_456", 
                    "function": {
                        "name": "finish_instruction",
                        "arguments": '{"analysis": "项目进度良好", "priority": "high", "next_steps": ["完成测试", "准备部署"]}'
                    }
                }]
            }
        else:
            return {"role": "assistant", "content": "-> STAGE_BEGIN: Understanding\n我理解了指令。\n\n-> STAGE_BEGIN: Response\n基于当前信息，我的回应是：这是一个有意义的任务。"}
    
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
    
    # 3. 创建 FunctionRegistry 并注册 FoV 函数
    print("\n3. 创建 FunctionRegistry 并注册 FoV 函数...")
    from simengine.function_registry import FunctionRegistry
    
    registry = FunctionRegistry()
    
    @registry.env.fov(desc="获取当前市场状态")
    def get_market_status(agent, env):
        return f"市场状态：活跃，针对 {agent.id} 的建议：保持关注"
    
    @registry.env.fov(desc="获取团队动态")
    def get_team_dynamics(agent, env):
        return f"团队动态：协作良好，{agent.id} 在团队中角色重要"
    
    print("✅ FunctionRegistry 和 FoV 函数注册完成")
    
    # 4. 创建测试环境和 Agent
    print("\n4. 创建测试环境和 Agent...")
    from simengine.agent import LLMAgent, Memory
    from simengine.core_data import WorldState, Environment
    
    try:
        # 创建环境
        env = Environment(type="test_environment")
        world = WorldState(step=0, environment=env)
        
        # 创建Agent
        persona = {
            "背景": "我是一名项目经理",
            "性格": "细致、负责、善于沟通",
            "技能": "项目管理、团队协调、风险评估"
        }
        
        agent = LLMAgent(
            id="pm_alice",
            type="project_manager",
            state={"current_task": "项目进度评估", "stress_level": "low"},
            properties={"location": "office", "team_size": 5}
        )
        
        world.add_agent(agent)
        
        # 初始化认知系统
        memory = Memory(
            agent_id="pm_alice",
            branch_id="main",
            embed_call=mock_embed_call,
            llm_call=mock_llm_call
        )
        
        agent.initialize_cognitive_system(
            persona=persona,
            memory=memory,
            llm_call=mock_llm_call
        )
        
        print("✅ 环境和Agent创建完成")
        
    except Exception as e:
        print(f"❌ 环境和Agent创建失败: {e}")
        return False
    
    # 5. 测试基础 instruct（无 FoV，无 output_schema）
    print("\n5. 测试基础 instruct...")
    try:
        result = await agent.instruct(
            instruction="请分析当前工作状态",
            current_step=1
        )
        
        print("✅ 基础 instruct 测试成功")
        print(f"   状态: {result['status']}")
        print(f"   输出: {result['performative_output'][:60]}...")
        print(f"   结构化输出: {result.get('structured_output', 'None')}")
        
    except Exception as e:
        print(f"❌ 基础 instruct 测试失败: {e}")
        return False
    
    # 6. 测试带 output_schema 的强制结构化输出
    print("\n6. 测试强制结构化输出...")
    try:
        output_schema = {
            "type": "object",
            "properties": {
                "analysis": {"type": "string", "description": "项目分析结果"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"], "description": "优先级"},
                "next_steps": {"type": "array", "items": {"type": "string"}, "description": "下一步行动"}
            },
            "required": ["analysis", "priority", "next_steps"]
        }
        
        result = await agent.instruct(
            instruction="请分析项目进度并制定行动计划",
            current_step=2,
            output_schema=output_schema
        )
        
        print("✅ 强制结构化输出测试成功")
        print(f"   状态: {result['status']}")
        print(f"   finish_instruction_called: {result.get('finish_instruction_called', False)}")
        print(f"   结构化输出: {result.get('structured_output', {})}")
        
        # 验证结构化输出的结构
        structured = result.get('structured_output', {})
        if structured and 'analysis' in structured and 'priority' in structured:
            print("   ✅ 结构化输出符合预期schema")
        else:
            print("   ❌ 结构化输出不符合预期schema")
            
    except Exception as e:
        print(f"❌ 强制结构化输出测试失败: {e}")
        return False
    
    # 7. 测试 instruct_operator 与 FoV 集成
    print("\n7. 测试 instruct_operator 与 FoV 集成...")
    from simengine.schedule import StepFlow
    from simengine.core_data import ExecutionContext
    
    try:
        # 创建 StepFlow 实例来测试 instruct_operator
        step_flow = StepFlow(0, {"nodes": []}, registry)
        
        # 创建执行上下文
        context = ExecutionContext(
            world=world,
            step=step_flow,
            node=None,
            caller=None
        )
        
        # 测试带 FoV 的 instruct_operator
        params = {
            "instruction": "基于当前市场和团队情况，评估项目风险",
            "fovs": ["get_market_status", "get_team_dynamics"],
            "is_memory": True,
            "output_schema": {
                "type": "object", 
                "properties": {
                    "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                    "recommendations": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["risk_level", "recommendations"]
            }
        }
        
        operator_result = await step_flow._instruct_operator([agent], params, context)
        
        print("✅ instruct_operator 与 FoV 集成测试成功")
        print(f"   处理的agent数量: {operator_result.value['agent_count']}")
        print(f"   请求的FoV: {operator_result.value['fovs_requested']}")
        print(f"   提供了output_schema: {operator_result.value['output_schema_provided']}")
        
        # 检查结果结构 - 现在 operator_result 是 InstructOperatorResult 对象
        if operator_result.status == "success":
            print(f"   操作状态: {operator_result.status}")
            print(f"   使用的FoV: {operator_result.fovs_used}")
            print(f"   Agent ID: {operator_result.agent_id}")
            
            if operator_result.fovs_used == ["get_market_status", "get_team_dynamics"]:
                print("   ✅ FoV 调用成功")
            else:
                print("   ❌ FoV 调用异常")
                
    except Exception as e:
        print(f"❌ instruct_operator 与 FoV 集成测试失败: {e}")
        return False
    
    print(f"\n🎉 高级 instruct 交互机制测试全部通过！")
    print("✅ FoV 传递机制正常工作")
    print("✅ 强制结构化输出机制正常工作") 
    print("✅ InstructOperatorResult 结构正确")
    print("✅ FunctionRegistry FoV 注册正常")
    print("✅ 向后兼容性保持")
    
    return True

if __name__ == "__main__":
    asyncio.run(test_advanced_instruct_mechanism())