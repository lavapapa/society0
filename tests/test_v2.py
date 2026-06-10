"""
Test script for SimEngine V2.
Demonstrates the new clean architecture with proper separation of concerns.
"""

import asyncio
import logging
import tempfile
import json
from pathlib import Path

# Import V2 engine
from libs.simengine.v2 import SimEngine, StatePatch
from libs.simengine.v2.test_config import test_config

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
            
            # Create engine
            engine = SimEngine(config_path=config_path, save_dir=temp_dir)
            
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
                """Rule to advance world time."""
                patches = []
                
                # Update global simulation time
                current_time = world_state.globals.get('simulation_time', 0)
                patches.append(StatePatch(
                    target_id="globals",
                    attribute="simulation_time",
                    operation="set", 
                    value=current_time + 1,
                    source="advance_world_time"
                ))
                
                # Random weather change
                import random
                if random.random() < 0.3:  # 30% chance
                    weather_options = ["sunny", "cloudy", "rainy"]
                    current_weather = environment.state.get('weather', 'sunny')
                    new_weather = random.choice([w for w in weather_options if w != current_weather])
                    
                    patches.append(StatePatch(
                        target_id="environment",
                        attribute="state.weather",
                        operation="set",
                        value=new_weather,
                        source="advance_world_time"
                    ))
                    
                    # Log weather change
                    patches.append(StatePatch(
                        target_id="globals",
                        attribute="world_events",
                        operation="add",
                        value=f"Weather changed to {new_weather}",
                        source="advance_world_time"
                    ))
                
                return patches
            
            # === Register Agent Functions ===
            
            @register.agent.rule(desc="Morning routine for agents")
            async def morning_routine(agent, world_state, params):
                """Morning routine rule for agents."""
                patches = []
                
                # Restore energy 
                patches.append(StatePatch(
                    target_id=f"agent_{agent.id}",
                    attribute="state.energy",
                    operation="set",
                    value=100,
                    source="morning_routine"
                ))
                
                # Update mood based on agent type
                if agent.type == "llm":
                    patches.append(StatePatch(
                        target_id=f"agent_{agent.id}",
                        attribute="state.mood",
                        operation="set",
                        value="refreshed",
                        source="morning_routine"
                    ))
                
                return patches
            
            @register.agent.rule(desc="Execute daily tasks for rule-based agents")
            async def execute_daily_tasks(agent, world_state, params):
                """Execute daily tasks for rule-based agents."""
                patches = []
                
                if agent.type == "rule":
                    task_queue = agent.state.get('task_queue', [])
                    
                    if task_queue:
                        # Execute first task
                        current_task = task_queue[0]
                        
                        # Simulate task execution
                        energy_cost = 10
                        patches.append(StatePatch(
                            target_id=f"agent_{agent.id}",
                            attribute="state.energy",
                            operation="add",
                            value=-energy_cost,
                            source="execute_daily_tasks"
                        ))
                        
                        # Remove completed task
                        remaining_tasks = task_queue[1:]
                        patches.append(StatePatch(
                            target_id=f"agent_{agent.id}",
                            attribute="state.task_queue",
                            operation="set",
                            value=remaining_tasks,
                            source="execute_daily_tasks"
                        ))
                        
                        # Add completed task to memory/log
                        patches.append(StatePatch(
                            target_id=f"agent_{agent.id}",
                            attribute="state.last_completed_task",
                            operation="set",
                            value=current_task,
                            source="execute_daily_tasks"
                        ))
                        
                        logger.info(f"Agent {agent.id} completed task: {current_task}")
                
                return patches
            
            # === Register Custom Selector ===
            
            @register.sched.selector(desc="Select agents with high energy")
            async def select_energetic_agents(world_state, params):
                """Select agents with energy > threshold."""
                threshold = params.get('energy_threshold', 50)
                selected = []
                
                for agent_id, agent in world_state.agents.items():
                    energy = agent.state.get('energy', 0)
                    if energy > threshold:
                        selected.append(agent_id)
                
                return selected
            
            # === Register Custom Operator ===
            
            @register.sched.operator(desc="Make agents interact socially")  
            async def social_interaction(agent_ids, world_state, params):
                """Custom operator for social interaction."""
                patches = []
                
                for agent_id in agent_ids:
                    agent = world_state.get_agent(agent_id)
                    if not agent:
                        continue
                    
                    # Add social interaction to agent's memory
                    interaction_log = f"Had social interaction at step {world_state.step}"
                    
                    patches.append(StatePatch(
                        target_id=f"agent_{agent_id}",
                        attribute="state.last_social_interaction",
                        operation="set",
                        value=interaction_log,
                        source="social_interaction"
                    ))
                    
                    # Improve mood slightly
                    current_mood = agent.state.get('mood', 'neutral')
                    if current_mood == 'sad':
                        new_mood = 'neutral'
                    elif current_mood == 'neutral':
                        new_mood = 'happy'
                    else:
                        new_mood = current_mood
                    
                    patches.append(StatePatch(
                        target_id=f"agent_{agent_id}",
                        attribute="state.mood",
                        operation="set",
                        value=new_mood,
                        source="social_interaction"
                    ))
                
                return patches
            
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
                logger.info(f"  Environment - Weather: {env_state.get('weather', 'unknown')}, Time: {engine.current_world_state.globals.get('simulation_time', 0)}")
            
            logger.info("Testing checkpoint/resume functionality...")
            
            # Test resume from checkpoint
            engine2 = SimEngine(config_path=config_path, save_dir=temp_dir)
            engine2.registry = engine.registry  # Share registered functions
            
            await engine2.resume(from_step=2)
            logger.info(f"Resumed engine from step 2, current step: {engine2.current_world_state.step}")
            
            # Run one more step
            await engine2.run(steps=1)
            logger.info(f"After additional step, current step: {engine2.current_world_state.step}")
            
            logger.info("Testing parallel world creation...")
            
            # Create parallel world
            parallel_engine = await engine.create_parallel_world(from_step=1, branch_name="test_branch")
            parallel_engine.registry = engine.registry  # Share registered functions
            
            # Run parallel world
            await parallel_engine.run(steps=2)
            logger.info(f"Parallel world final step: {parallel_engine.current_world_state.step}")
            
            # Get experiment info
            info = engine.get_experiment_info()
            logger.info("Experiment information:")
            logger.info(f"  Config: {info['config_path']}")
            logger.info(f"  Save dir: {info['save_dir']}")
            logger.info(f"  Initialized: {info['is_initialized']}")
            logger.info(f"  Registry stats: {info['registry_stats']}")
            
            logger.info("=== V2 Architecture Test Completed Successfully! ===")
            
        except Exception as e:
            logger.error(f"Test failed: {e}")
            raise
        
        finally:
            # Clean up
            Path(config_path).unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(test_v2_architecture())