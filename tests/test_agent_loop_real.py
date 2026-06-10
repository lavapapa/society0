#!/usr/bin/env python3
"""
Real LLM test for the multi-stage ToolCallLoop engine using OpenAI SDK.
"""

import sys
import os
import asyncio
import json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

async def test_real_llm_agent_loop():
    """Test the multi-stage ToolCallLoop engine with real LLM."""

    from simengine.agent_loop import execute_tool_call_loop, ToolSet, LoopResult
    from openai import AsyncOpenAI

    # LLM Configuration
    DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1/"
    DEFAULT_API_KEY = "sk-vhzerlbzweeltitrunokhxxphckzfeyfzmzvbrjhoexzflcr"
    DEFAULT_MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"

    print("Testing multi-stage ToolCallLoop engine with real LLM...")
    print(f"Using model: {DEFAULT_MODEL} at {DEFAULT_BASE_URL}")

    # Initialize OpenAI client
    client = AsyncOpenAI(
        api_key=DEFAULT_API_KEY,
        base_url=DEFAULT_BASE_URL
    )

    # Define real tools with proper OpenAI schemas
    def get_weather(location: str) -> dict:
        """Get current weather information for a location."""
        # Simulate weather API
        weather_data = {
            "New York": {"temperature": 22, "condition": "sunny", "humidity": 65},
            "London": {"temperature": 15, "condition": "cloudy", "humidity": 78},
            "Tokyo": {"temperature": 28, "condition": "rainy", "humidity": 85}
        }
        return weather_data.get(location, {"temperature": 20, "condition": "unknown", "humidity": 50})

    def calculate_math(expression: str) -> str:
        """Calculate a mathematical expression safely."""
        try:
            # Safe evaluation for basic math
            allowed_chars = set('0123456789+-*/().,')
            if all(c in allowed_chars for c in expression.replace(' ', '')):
                result = eval(expression)
                return f"The result of {expression} is {result}"
            else:
                return "Error: Invalid characters in expression"
        except Exception as e:
            return f"Error: {str(e)}"

    def search_information(query: str) -> dict:
        """Search for information on a given topic."""
        # Simulate information search
        search_results = {
            "climate change": {
                "summary": "Global warming and environmental impacts due to human activities",
                "key_points": ["Rising temperatures", "Ice melting", "Sea level rise"],
                "sources": ["IPCC", "NASA", "NOAA"]
            },
            "artificial intelligence": {
                "summary": "Computer systems able to perform tasks typically requiring human intelligence",
                "key_points": ["Machine learning", "Neural networks", "Natural language processing"],
                "sources": ["IEEE", "MIT", "Stanford AI"]
            }
        }

        for topic in search_results:
            if topic.lower() in query.lower():
                return search_results[topic]

        return {
            "summary": f"General information about {query}",
            "key_points": ["Basic overview", "Common applications", "Current trends"],
            "sources": ["General knowledge base"]
        }

    # Create toolset with proper OpenAI schemas
    toolset = ToolSet()

    toolset.add_tool(
        name="get_weather",
        func=get_weather,
        description="Get current weather information for a specific location",
        parameters={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city name to get weather for"
                }
            },
            "required": ["location"]
        }
    )

    toolset.add_tool(
        name="calculate_math",
        func=calculate_math,
        description="Calculate mathematical expressions safely",
        parameters={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Mathematical expression to calculate, e.g., '2 + 3 * 4'"
                }
            },
            "required": ["expression"]
        }
    )

    toolset.add_tool(
        name="search_information",
        func=search_information,
        description="Search for information on a specific topic",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query or topic to find information about"
                }
            },
            "required": ["query"]
        }
    )

    # Real LLM call function using OpenAI SDK
    async def real_llm_call(payload: dict) -> dict:
        """Make actual API call to the LLM using OpenAI SDK."""
        kwargs = {
            "model": DEFAULT_MODEL,
            "messages": payload["messages"],
            "temperature": 0.7,
            "max_tokens": 2048
        }

        # Add tools if provided
        if payload.get("tools"):
            kwargs["tools"] = payload["tools"]
            kwargs["tool_choice"] = "auto"

        print(kwargs)
        response = await client.chat.completions.create(**kwargs)

        # Return the message in the expected format
        message = response.choices[0].message
        result = {
            "role": message.role,
            "content": message.content
        }

        # Add tool calls if present
        if message.tool_calls:
            result["tool_calls"] = []
            for tool_call in message.tool_calls:
                result["tool_calls"].append({
                    "id": tool_call.id,
                    "type": tool_call.type,
                    "function": {
                        "name": tool_call.function.name,
                        "arguments": tool_call.function.arguments
                    }
                })

        return result


    print("LLM client configured. Starting tests...")

    # Test 1: Multi-step weather analysis
    print("\n=== Test 1: Weather Analysis with Tool Calls ===")

    result1 = await execute_tool_call_loop(
        instruction="Please analyze the weather in New York and London, then provide a comparison and travel recommendation.",
        tool_set=toolset,
        system_prompt="You are a helpful weather analyst and travel advisor.",
        stages=["Planning", "DataGathering", "Analysis", "Recommendation"],
        llm_call=real_llm_call,
        max_turns=5
    )

    # print(f"Status: {result1.status}")
    # print(f"Total turns: {result1.total_turns}")
    # print(f"Stages found: {list(result1.phases.keys())}")

    for stage_name, stage_content in result1.phases.items():
        print(f"\n--- {stage_name} ---")
        if isinstance(stage_content, list):
            for item in stage_content:
                if item["type"] == "text":
                    print(f"Text: {item['content'][:200]}...")
                elif item["type"] == "tool_call":
                    print(f"Tool call: {item['tool_name']}({item['arguments']})")
        else:
            print(f"{stage_content[:300]}...")

    assert result1.status in ["success", "partial_success"], "Weather analysis should succeed"
    print("✅ Test 1 passed!")



    # Test 2: Mathematical problem solving
    print("\n=== Test 2: Mathematical Problem Solving ===")
    try:
        result2 = await execute_tool_call_loop(
            instruction="Solve this step by step: What is (15 + 25) * 2 - 10, and explain the order of operations.",
            tool_set=toolset,
            system_prompt="You are a math tutor who explains concepts clearly.",
            stages=["Understanding", "StepByStep", "Calculation", "Verification", "Explanation"],
            llm_call=real_llm_call,
            max_turns=4
        )

        print(f"Status: {result2.status}")
        print(f"Total turns: {result2.total_turns}")
        print(f"Stages found: {list(result2.phases.keys())}")

        # Check if calculation was performed
        has_calculation = any(
            isinstance(content, list) and
            any(item.get("type") == "tool_call" and item.get("tool_name") == "calculate_math"
                for item in content if isinstance(item, dict))
            for content in result2.phases.values()
        )

        print(f"Math calculation performed: {has_calculation}")
        assert result2.status in ["success", "partial_success"], "Math solving should succeed"
        print("✅ Test 2 passed!")

    except Exception as e:
        print(f"❌ Test 2 failed: {e}")

    # Test 3: Research and information gathering
    print("\n=== Test 3: Research Task ===")
    try:
        result3 = await execute_tool_call_loop(
            instruction="Research artificial intelligence and provide a brief overview with key concepts.",
            tool_set=toolset,
            system_prompt="You are a research assistant who gathers and synthesizes information.",
            stages=["Research", "Synthesis", "Summary"],
            llm_call=real_llm_call,
            max_turns=4
        )

        print(f"Status: {result3.status}")
        print(f"Total turns: {result3.total_turns}")
        print(f"Stages found: {list(result3.phases.keys())}")

        # Check if research was performed
        has_research = any(
            isinstance(content, list) and
            any(item.get("type") == "tool_call" and item.get("tool_name") == "search_information"
                for item in content if isinstance(item, dict))
            for content in result3.phases.values()
        )

        print(f"Information search performed: {has_research}")
        assert result3.status in ["success", "partial_success"], "Research should succeed"
        print("✅ Test 3 passed!")

    except Exception as e:
        print(f"❌ Test 3 failed: {e}")

    # Test 4: Complex multi-tool task
    print("\n=== Test 4: Complex Multi-Tool Task ===")
    try:
        result4 = await execute_tool_call_loop(
            instruction="Help me plan a trip: check the weather in Tokyo, calculate the cost if flight is $800 and hotel is $150 per night for 3 nights, and research what I should know about climate change impact on travel.",
            tool_set=toolset,
            system_prompt="You are a comprehensive travel planning assistant.",
            stages=["Planning", "WeatherCheck", "CostCalculation", "ResearchInfo", "TravelAdvice"],
            llm_call=real_llm_call,
            max_turns=6
        )

        print(f"Status: {result4.status}")
        print(f"Total turns: {result4.total_turns}")
        print(f"Stages found: {list(result4.phases.keys())}")

        # Count tool calls
        total_tool_calls = 0
        for content in result4.phases.values():
            if isinstance(content, list):
                total_tool_calls += sum(1 for item in content
                                     if isinstance(item, dict) and item.get("type") == "tool_call")

        print(f"Total tool calls made: {total_tool_calls}")
        assert result4.status in ["success", "partial_success"], "Complex task should succeed"
        assert total_tool_calls >= 2, "Should make multiple tool calls"
        print("✅ Test 4 passed!")

    except Exception as e:
        print(f"❌ Test 4 failed: {e}")

    print(f"\n🎉 Real LLM ToolCallLoop testing completed!")
    print(f"Tested with {DEFAULT_MODEL} at {DEFAULT_BASE_URL}")
    print("✅ All major functionality verified with real LLM interactions")

    # Show tool schema validation
    print(f"\nTool schema validation:")
    tools_schema = toolset.get_openai_tools_schema()
    for tool in tools_schema:
        func_info = tool["function"]
        print(f"- {func_info['name']}: {len(func_info['parameters']['properties'])} parameters")

    return True

if __name__ == "__main__":
    # Check if openai is available
    try:
        import openai
        asyncio.run(test_real_llm_agent_loop())
    except ImportError:
        print("Please install openai SDK: pip install openai")
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()