#!/usr/bin/env python3
"""
Final Integration Test

Test that all components of the final integration work together:
1. World.instruct_agent method
2. Refactored _instruct_operator
3. Complete LLMAgent.instruct workflow 
4. World.assemble_agent_actionset
5. Environment.snapshot()
6. PersistenceManager unified checkpointing
"""

import sys
import os
import asyncio
import tempfile
from unittest.mock import AsyncMock

# Add project path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))

def test_world_instruct_agent():
    """Test World.instruct_agent method"""
    print("=== Test: World.instruct_agent ===")
    
    import asyncio
    import tempfile
    from simengine.core_data import World
    from simengine.function_registry import FunctionRegistry
    
    # Create temporary log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # Create World and agent
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "llm")
        
        # Create function registry and FoV
        registry = FunctionRegistry()
        
        @registry.env.fov("test_fov")
        def test_fov(agent, env):
            return f"Agent {agent.id} sees environment {env.type}"
        
        world.set_function_registry(registry)
        
        # Mock LLM call function
        async def mock_llm_call(payload):
            return {
                "content": "I understand the instruction and will respond accordingly.",
                "tool_calls": []
            }
        
        # Initialize agent's cognitive system
        alice = world.get_agent("alice")  # This now returns LLMAgent for llm archetype
        alice.initialize_cognitive_system(
            persona={"name": "Alice", "role": "student"},
            memory=None,  # No memory for this test
            llm_call=mock_llm_call
        )
        
        async def run_test():
            result = await world.instruct_agent(
                agent_id="alice",
                instruction="Please introduce yourself",
                fovs=["test_fov"],
                action_tags=None
            )
            return result
        
        result = asyncio.run(run_test())
        
        # Verify results
        assert result["status"] == "success", f"Expected success, got {result['status']}"
        assert result["agent_id"] == "alice", f"Wrong agent ID: {result['agent_id']}"
        assert "performative_output" in result, "Missing performative_output"
        
        world.event_logger.close()
        
        print("✅ World.instruct_agent test passed")
        print(f"   Result status: {result['status']}")
        print(f"   Agent ID: {result['agent_id']}")
        return True
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_action_assembly():
    """Test World.assemble_agent_actionset"""
    print("=== Test: Action Assembly ===")
    
    import tempfile
    from simengine.core_data import World
    from simengine.function_registry import FunctionRegistry
    
    # Create temporary log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # Create World and agent
        world = World(step=0, event_log_path=log_path)
        world.add_agent_data("alice", "student", "llm")
        
        # Create function registry with actions
        registry = FunctionRegistry()
        
        @registry.agent.action("test_action")
        async def test_action(agent, world, params):
            return "Test action executed"
        
        world.set_function_registry(registry)
        
        # Get agent and assemble actionset
        alice = world.get_agent("alice")  # This now returns LLMAgent for llm archetype
        world.assemble_agent_actionset(alice)
        
        # Verify actionset was assembled
        assert hasattr(alice, '_actionset'), "Agent should have _actionset attribute"
        assert alice._actionset is not None, "ActionSet should not be None"
        
        # Check if actions were added
        actions_count = len(alice._actionset.actions)
        assert actions_count > 0, f"Expected actions, got {actions_count}"
        
        world.event_logger.close()
        
        print("✅ Action Assembly test passed")
        print(f"   Actions assembled: {actions_count}")
        return True
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_environment_snapshot():
    """Test Environment.snapshot() and restore_from_snapshot()"""
    print("=== Test: Environment Snapshot ===")
    
    import tempfile
    from simengine.core_data import World
    
    # Create temporary log file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        log_path = f.name
    
    try:
        # Create World
        world = World(step=0, event_log_path=log_path)
        world.set_environment_type("test_environment")
        
        # Get environment and modify state
        env = world.get_environment()
        env.state["weather"] = "sunny"
        env.state["temperature"] = 25
        
        # Create snapshot
        snapshot = env.snapshot()
        
        # Verify snapshot structure
        assert "type" in snapshot, "Snapshot should include type"
        assert "state" in snapshot, "Snapshot should include state"
        assert snapshot["type"] == "test_environment", f"Wrong type: {snapshot['type']}"
        assert snapshot["state"]["weather"] == "sunny", f"Wrong weather: {snapshot['state']['weather']}"
        assert snapshot["state"]["temperature"] == 25, f"Wrong temperature: {snapshot['state']['temperature']}"
        
        # Test restore
        env.state.clear()
        env.state["new_data"] = "should be replaced"
        
        env.restore_from_snapshot(snapshot)
        
        # Verify restore worked
        assert env.state["weather"] == "sunny", "Weather should be restored"
        assert env.state["temperature"] == 25, "Temperature should be restored"
        assert "new_data" not in env.state, "New data should be cleared"
        
        world.event_logger.close()
        
        print("✅ Environment Snapshot test passed")
        print(f"   Snapshot type: {snapshot['type']}")
        print(f"   State restored correctly")
        return True
        
    finally:
        world.close()
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_persistence_manager():
    """Test PersistenceManager unified checkpointing"""
    print("=== Test: PersistenceManager ===")
    
    import tempfile
    from simengine.core_data import World
    from simengine.persistence import PersistenceManager
    from simengine.function_registry import FunctionRegistry
    from simengine.schedule import Schedule
    
    # Create temporary directories
    with tempfile.TemporaryDirectory() as temp_dir:
        log_path = os.path.join(temp_dir, "events.jsonl")
        
        try:
            # Create World with data
            world = World(step=5, event_log_path=log_path)
            world.add_agent_data("alice", "student", "llm")
            world.add_agent_data("bob", "teacher", "rule")
            
            # Modify some state
            alice = world.get_agent("alice")
            alice.state["knowledge"] = "lots"
            alice.properties["age"] = 20
            
            env = world.get_environment()
            env.state["weather"] = "rainy"
            
            # Create schedule and persistence manager
            registry = FunctionRegistry()
            schedule = Schedule({"nodes": []}, registry)
            persistence = PersistenceManager(temp_dir)
            
            async def run_checkpoint_test():
                # Save checkpoint
                await persistence.save_checkpoint(world, schedule)
                
                # Verify checkpoint files were created
                checkpoints = await persistence.get_available_checkpoints()
                assert 5 in checkpoints, f"Step 5 not in checkpoints: {checkpoints}"
                
                return True
            
            result = asyncio.run(run_checkpoint_test())
            
            world.event_logger.close()
            
            print("✅ PersistenceManager test passed")
            print(f"   Checkpoint created for step 5")
            return result
            
        finally:
            world.close()


def main():
    """Run all final integration tests"""
    print("🚀 Start Final Integration Tests\n")
    
    tests = [
        test_world_instruct_agent,
        test_action_assembly, 
        test_environment_snapshot,
        test_persistence_manager,
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
                print(f"❌ {test.__name__} test failed\n")
        except Exception as e:
            failed += 1
            print(f"❌ {test.__name__} test exception: {e}")
            import traceback
            traceback.print_exc()
            print()
    
    print(f"📊 Test Results: Passed {passed}/{len(tests)} tests")
    
    if failed == 0:
        print("🎉 Final Integration Tests Passed! All new components work correctly.")
        return True
    else:
        print(f"💥 {failed} tests failed, need to fix issues.")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)