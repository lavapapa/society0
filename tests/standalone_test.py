"""
Standalone test for SimEngine V2 - bypassing the old package structure.
"""

import asyncio
import logging
import tempfile
import json
import sys
from pathlib import Path

# Add current directory to path to import V2 modules directly
sys.path.insert(0, str(Path(__file__).parent))

# Import V2 modules directly
from sim_engine import SimEngine
from core_data import StatePatch
from test_config import test_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def test_v2_architecture():
    """Test the V2 architecture with a complete simulation."""

    # Create temporary config file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(test_config, f, indent=2)
        config_path = f.name

    # Create temporary experiment directory
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            logger.info("=== Testing SimEngine V2 Architecture ===")

            # Create engine with new API
            engine = SimEngine(save_dir="./_test_project", base_config=test_config)

            # Get registry for function registration
            register = engine.register

            # === Register Environment Functions ===

            @register.env.fov(desc="See current weather and environment state")
            async def observe_environment(environment, world_state, params):
                """FoV function to observe environment."""
                state = environment.state
                return [
                    f"The weather is {state.get('weather', 'unknown')}",
                    f"Temperature: {state.get('temperature', 0)}°C",
                    f"Available resources: {state.get('resources', {})}"
                ]

            @register.env.rule(desc="Advance world time and update environment")
            async def advance_world_time(environment, world_state, params):
                """Rule to advance world time - directly modifies world_state."""
                # Update global simulation time directly
                current_time = world_state.globals.get('simulation_time', 0)
                world_state.globals['simulation_time'] = current_time + 1

                # Random weather change
                import random
                if random.random() < 0.3:  # 30% chance
                    weather_options = ["sunny", "cloudy", "rainy"]
                    current_weather = environment.state.get('weather', 'sunny')
                    new_weather = random.choice([w for w in weather_options if w != current_weather])

                    # Directly modify environment state
                    environment.state['weather'] = new_weather

                    # Log weather change directly
                    if 'world_events' not in world_state.globals:
                        world_state.globals['world_events'] = []
                    world_state.globals['world_events'].append(f"Weather changed to {new_weather}")

            # === Register Agent Functions ===

            @register.agent.rule(desc="Morning routine for agents")
            async def morning_routine(agent, world_state, params):
                """Morning routine rule for agents - directly modifies agent."""
                # Restore energy directly
                agent.state['energy'] = 100

                # Update mood based on agent type
                if agent.type == "llm":
                    agent.state['mood'] = "refreshed"

            @register.agent.rule(desc="Execute daily tasks for rule-based agents")
            async def execute_daily_tasks(agent, world_state, params):
                """Execute daily tasks for rule-based agents - directly modifies agent."""
                if agent.type == "rule":
                    task_queue = agent.state.get('task_queue', [])

                    if task_queue:
                        # Execute first task
                        current_task = task_queue[0]

                        # Simulate task execution - directly modify agent
                        energy_cost = 10
                        agent.state['energy'] = agent.state.get('energy', 100) - energy_cost

                        # Remove completed task
                        remaining_tasks = task_queue[1:]
                        agent.state['task_queue'] = remaining_tasks

                        # Add completed task to memory/log
                        agent.state['last_completed_task'] = current_task

                        logger.info(f"Agent {agent.id} completed task: {current_task}")

            # === Test the Engine ===

            logger.info("Testing configuration validation...")
            validation_issues = await engine.validate_configuration()
            if validation_issues:
                logger.warning("Configuration issues found:")
                for issue in validation_issues:
                    logger.warning(f"  - {issue}")
            else:
                logger.info("Configuration validated successfully")

            logger.info("Testing engine initialization...")
            await engine._initialize()

            logger.info("Initial world state:")
            if engine.current_world_state:
                summary = engine.current_world_state.get_state_summary()
                logger.info(f"  Step: {summary['step']}")
                logger.info(f"  Agents: {summary['agent_count']} ({summary['agent_types']})")
                logger.info(f"  Environment: {summary['environment_type']}")

                # Show initial agent states
                for agent_id, agent in engine.current_world_state.agents.items():
                    logger.info(f"  Agent {agent_id} - Energy: {agent.state.get('energy', 0)}, Type: {agent.type}")

            logger.info("Running simulation for 3 steps...")
            await engine.run(steps=3)

            logger.info("Final world state:")
            if engine.current_world_state:
                summary = engine.current_world_state.get_state_summary()
                logger.info(f"  Final Step: {summary['step']}")
                logger.info(f"  Agents: {summary['agent_count']}")

                # Show some agent states
                for agent_id, agent in engine.current_world_state.agents.items():
                    logger.info(f"  Agent {agent_id} - Energy: {agent.state.get('energy', 0)}, Mood: {agent.state.get('mood', 'unknown')}")

                # Show environment state
                env_state = engine.current_world_state.environment.state
                logger.info(f"  Environment - Weather: {env_state.get('weather', 'unknown')}")
                logger.info(f"  Global time: {engine.current_world_state.globals.get('simulation_time', 0)}")

                # Show world events
                events = engine.current_world_state.globals.get('world_events', [])
                if events:
                    logger.info(f"  World events: {events}")

            logger.info("Testing checkpoint functionality...")

            # Get available checkpoints
            checkpoints = await engine.persistence_manager.get_available_checkpoints()
            logger.info(f"Available checkpoints: {checkpoints}")

            # Test resume from checkpoint
            if checkpoints:
                engine2 = SimEngine(save_dir=temp_dir, base_config=test_config)
                # Share registered functions by adding the registry
                engine2.add_registry(engine.register)

                await engine2.resume(from_step=max(checkpoints))
                logger.info(f"Successfully resumed from step {engine2.current_world_state.step}")

            # Get experiment info
            info = engine.get_experiment_info()
            logger.info("Experiment information:")
            logger.info(f"  Save dir: {info['save_dir']}")
            logger.info(f"  Registry stats: {info['registry_stats']}")

            logger.info("=== V2 Architecture Test Completed Successfully! ===")

            return True

        except Exception as e:
            logger.error(f"Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            # Clean up
            Path(config_path).unlink(missing_ok=True)


if __name__ == "__main__":
    success = asyncio.run(test_v2_architecture())
    if success:
        print("✅ V2 Architecture test passed!")
    else:
        print("❌ V2 Architecture test failed!")
        sys.exit(1)