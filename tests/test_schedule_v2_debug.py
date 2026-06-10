#!/usr/bin/env python3
"""
Schedule V2 Debug Test - Simple Version to Debug Issues
"""

import sys
import os
import asyncio
import tempfile

# Add project path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

def test_simple_debug():
    """Simple debug test to identify issues"""
    print("=== Debug Test: Simple Schedule V2 Execution ===")

    import tempfile
    from simengine.core_data import World, ExecutionContext
    from simengine.function_registry import FunctionRegistry
    from simengine.schedule import StepFlow, BaseOperatorResult
    from simengine.context_stack import ContextStack

    # Create temporary log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name

    try:
        # Create World with one agent
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "llm")

        # Create simple function registry
        registry = FunctionRegistry()

        @registry.sched.behavior("simple_action")
        async def simple_action(agent, env, test_param="hello"):
            """Simple test action"""
            print(f"Simple behavior called for agent={agent.id}, test_param={test_param}")

            result = BaseOperatorResult(
                agent_id=agent.id,
                status="success",
                value={"message": "simple behavior executed", "param": test_param},
                metadata={"test": True}
            )
            print(f"Returning result: {result}")
            return result

        # Simple V2 configuration
        config = {
            "nodes": [
                {
                    "id": "simple_node",
                    "selector": {"type": "by_archetype", "archetype": "llm"},
                    "operators": [
                        {
                            "id": "simple_op",
                            "type": "behavior",
                            "name": "simple_action",
                            "test_param": "hello"
                        }
                    ],
                    "dependencies": []
                }
            ]
        }

        # Create StepFlow
        step_flow = StepFlow(step_number=0, step_config=config, function_registry=registry)
        print(f"Created StepFlow with {len(step_flow.step_nodes)} nodes")

        # Set up execution context
        world.set_context_stack(ContextStack().push_step("debug_test"))
        context = ExecutionContext(world=world, step=step_flow, node=None, caller="debug_test")

        # Execute
        async def run_debug():
            print("Executing step...")
            result = await step_flow.execute(world, context)
            print(f"Step execution result: {result}")
            return result

        result = asyncio.run(run_debug())

        # Debug analysis
        print(f"\n=== Debug Analysis ===")
        print(f"World agents: {list(world.agents_data.keys())}")
        print(f"Step result keys: {list(result.keys())}")

        if "results" in result:
            step_results = result["results"]
            print(f"Number of node results: {len(step_results)}")

            if step_results:
                node_result = step_results[0]
                print(f"Node result keys: {list(node_result.keys())}")
                print(f"Operator results count: {len(node_result.get('operator_results', []))}")

                for i, op_result in enumerate(node_result.get('operator_results', [])):
                    print(f"  Operator result {i}: status={getattr(op_result, 'status', 'unknown')}, value={getattr(op_result, 'value', 'none')}")

        # Check agent state
        alice = world.get_agent("alice")
        print(f"Alice state: {dict(alice.state)}")

        world.event_logger.close()
        return True

    except Exception as e:
        print(f"❌ Debug test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


if __name__ == "__main__":
    success = test_simple_debug()
    if success:
        print("✅ Debug test completed")
    else:
        print("❌ Debug test failed")
    sys.exit(0 if success else 1)
