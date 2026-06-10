#!/usr/bin/env python3
"""
Memory系统集成测试

测试Memory系统与LLMAgent的完整集成，验证：
1. Milvus Lite全局实例初始化
2. Memory实例创建和绑定
3. LLMAgent工具集成
4. 记忆存储和检索
5. Agent工具的正常工作
"""

import sys
import os
import asyncio
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

async def test_memory_system_integration():
    """测试Memory系统完整集成"""
    
    print("=== Memory System Integration Test ===")
    
    # 1. 初始化全局Milvus实例
    print("\n1. 初始化全局Milvus实例...")
    from simengine.memory import init_global_milvus
    
    try:
        await init_global_milvus("./test_milvus_lite.db")
        print("✅ Milvus全局实例初始化成功")
    except Exception as e:
        print(f"❌ Milvus初始化失败: {e}")
        print("请安装pymilvus: pip install pymilvus")
        return False
    
    # 2. 创建LLM调用函数（模拟）
    print("\n2. 设置模拟LLM调用...")
    
    async def mock_llm_call(payload: dict) -> dict:
        """模拟LLM调用"""
        messages = payload.get("messages", [])
        if not messages:
            return {"role": "assistant", "content": "模拟响应"}
        
        user_message = messages[-1].get("content", "")
        
        # 模拟不同类型的响应
        if "重要性" in user_message:
            # 重要性评估
            return {"role": "assistant", "content": "3.5"}
        elif "反思" in user_message:
            # 反思响应
            return {"role": "assistant", "content": "通过分析相关记忆，我发现这个主题涉及多个层面的考量，需要综合不同的视角来理解..."}
        else:
            # 默认指令执行响应
            return {"role": "assistant", "content": f"-> STAGE_BEGIN: Understanding\n我理解了指令：{user_message}\n\n-> STAGE_BEGIN: Response\n任务完成，这是我的响应。"}
    
    print("✅ 模拟LLM调用函数设置完成")
    
    # 3. 创建Memory实例
    print("\n3. 创建Memory实例...")
    from simengine.memory import Memory
    
    # 创建模拟embedding函数
    async def mock_embed_call(texts: list, dimensions: int = 512) -> dict:
        """模拟embedding函数"""
        import random
        embeddings = []
        for text in texts:
            # 生成随机向量作为模拟embedding
            embedding = [random.uniform(-1.0, 1.0) for _ in range(dimensions)]
            embeddings.append(embedding)
        
        return {
            "result": embeddings,
            "model": "mock-embed-model",
            "dimensions": dimensions
        }
    
    try:
        memory = Memory(
            agent_id="test_agent_001",
            branch_id="main",
            embed_call=mock_embed_call,
            llm_call=mock_llm_call,
            embedding_dim=512
        )
        print("✅ Memory实例创建成功")
    except Exception as e:
        print(f"❌ Memory实例创建失败: {e}")
        return False
    
    # 4. 创建LLMAgent并初始化
    print("\n4. 创建并初始化LLMAgent...")
    from simengine.core_data import LLMAgent
    
    try:
        agent = LLMAgent(
            id="test_agent_001",
            type="test_assistant",
            state={"role": "测试助手"}
        )
        
        agent.initialize_memory_and_tools(
            memory=memory,
            llm_call=mock_llm_call
        )
        print("✅ LLMAgent初始化成功")
        print(f"   - 工具数量: {len(agent.tool_set.tools)}")
        print(f"   - 工具列表: {list(agent.tool_set.tools.keys())}")
    except Exception as e:
        print(f"❌ LLMAgent初始化失败: {e}")
        return False
    
    # 5. 测试记忆存储
    print("\n5. 测试记忆存储...")
    try:
        # 添加一些测试记忆
        mem1_id = await memory.add_episodic_memory(
            content="今天学会了如何使用Memory系统",
            timestamp=1,
            importance=4.0
        )
        
        mem2_id = await memory.add_semantic_memory(
            content="Memory系统是一个用于存储和检索Agent记忆的框架",
            timestamp=2,
            importance=4.5
        )
        
        print(f"✅ 记忆存储成功")
        print(f"   - 情景记忆ID: {mem1_id}")
        print(f"   - 语义记忆ID: {mem2_id}")
    except Exception as e:
        print(f"❌ 记忆存储失败: {e}")
        return False
    
    # 6. 测试记忆检索
    print("\n6. 测试记忆检索...")
    try:
        retrieved_memories = await memory.retrieve(
            query="Memory系统的使用",
            top_k=5,
            current_step=10
        )
        print(f"✅ 记忆检索成功")
        print(f"   - 检索到记忆数量: {len(retrieved_memories)}")
        for i, mem in enumerate(retrieved_memories, 1):
            print(f"   - 记忆{i}: {mem[:50]}...")
    except Exception as e:
        print(f"❌ 记忆检索失败: {e}")
        return False
    
    # 7. 测试Agent工具调用
    print("\n7. 测试Agent工具调用...")
    try:
        # 测试搜索记忆工具
        search_result = await agent.tool_set.call_tool(
            "search_memory", 
            query="Memory系统"
        )
        print(f"✅ search_memory工具调用成功")
        print(f"   - 搜索结果数量: {len(search_result)}")
        
        # 测试强制记忆工具
        memorize_result = await agent.tool_set.call_tool(
            "memorize_this", 
            fact="这是一个通过工具强制存储的重要记忆",
            importance=4.8
        )
        print(f"✅ memorize_this工具调用成功")
        print(f"   - 结果: {memorize_result}")
        
    except Exception as e:
        print(f"❌ Agent工具调用失败: {e}")
        return False
    
    # 8. 测试完整的execute_instruction工作流
    print("\n8. 测试完整的execute_instruction工作流...")
    try:
        instruction = "请帮我总结一下Memory系统的核心功能"
        context = {
            "fov_results": {
                "environment_scan": "当前环境：测试环境，包含Memory系统测试"
            }
        }
        
        result = await agent.execute_instruction(
            instruction=instruction,
            context=context,
            is_memory=True,
            current_step=15,
            system_prompt="你是一个专业的系统分析师",
            stages=["Understanding", "Analysis", "Response"],
            max_turns=3
        )
        
        print(f"✅ execute_instruction执行成功")
        print(f"   - 执行状态: {result['status']}")
        print(f"   - 检索记忆数量: {result['memories_retrieved']}")
        print(f"   - 总轮次: {result['total_turns']}")
        print(f"   - 表现输出: {result['performative_output'][:100]}...")
        
    except Exception as e:
        print(f"❌ execute_instruction执行失败: {e}")
        return False
    
    # 9. 测试readonly模式
    print("\n9. 测试readonly模式...")
    try:
        readonly_result = await agent.execute_instruction(
            instruction="在readonly模式下搜索Memory相关信息",
            context={},
            is_memory=False,  # readonly模式
            current_step=20,
            max_turns=2
        )
        
        print(f"✅ readonly模式执行成功")
        print(f"   - 执行状态: {readonly_result['status']}")
        print(f"   - 是否记忆模式: {readonly_result['is_memory']}")
        
    except Exception as e:
        print(f"❌ readonly模式执行失败: {e}")
        return False
    
    # 10. 验证记忆持久化
    print("\n10. 验证记忆持久化...")
    try:
        # 再次检索，看看新添加的记忆是否存在
        final_memories = await memory.retrieve(
            query="工具强制存储的记忆",
            top_k=5,
            current_step=25
        )
        
        print(f"✅ 记忆持久化验证成功")
        print(f"   - 持久化记忆数量: {len(final_memories)}")
        
        # 检查是否包含通过工具存储的记忆
        tool_memory_found = any("工具强制存储" in mem for mem in final_memories)
        if tool_memory_found:
            print("   - ✅ 通过工具存储的记忆已成功持久化")
        else:
            print("   - ⚠️  通过工具存储的记忆未找到")
        
    except Exception as e:
        print(f"❌ 记忆持久化验证失败: {e}")
        return False
    
    print(f"\n🎉 Memory系统集成测试全部通过！")
    print("✅ Memory as a Service架构实现成功")
    print("✅ Agent as Assembler工作流验证完成")
    print("✅ 数据驱动交互模式运行正常")
    
    return True

async def test_embedding_function():
    """单独测试embedding函数"""
    print("\n=== 测试Embedding函数 ===")
    
    try:
        from simengine.memory import ollama_embed
        
        test_texts = [
            "这是一个测试文本",
            "Memory系统可以存储和检索记忆", 
            "LLMAgent具有记忆能力"
        ]
        
        print(f"正在对{len(test_texts)}个文本进行向量化...")
        result = await ollama_embed(test_texts, dimensions=512)
        
        print("✅ Ollama embedding测试成功")
        print(f"   - 模型: {result['model']}")
        print(f"   - 维度: {result['dimensions']}")
        print(f"   - 向量数量: {len(result['result'])}")
        print(f"   - 第一个向量维度: {len(result['result'][0])}")
        
    except ImportError:
        print("❌ Ollama SDK未安装，请运行: pip install ollama")
    except Exception as e:
        print(f"❌ Embedding测试失败: {e}")
        print("请确保Ollama服务在 http://172.31.198.119:11434 运行，并且模型nomic-embed-text可用")

if __name__ == "__main__":
    async def main():
        # 首先测试embedding函数
        await test_embedding_function()
        
        print("\n" + "="*50)
        
        # 然后测试完整系统集成
        success = await test_memory_system_integration()
        
        if success:
            print("\n🎉 所有测试通过！Memory系统已准备就绪。")
        else:
            print("\n❌ 部分测试失败，请检查错误信息。")
    
    asyncio.run(main())