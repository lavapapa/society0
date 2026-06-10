"""
Example usage of the Society0 simulation engine.
This script demonstrates how to set up and run a complete simulation.
"""

import asyncio
import logging
from pathlib import Path

# Import the simulation engine
from libs.simengine import Experiment
from libs.simengine.example_config import experiment_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def main():
    """Main example function demonstrating the simulation engine."""

    # Create experiment
    exp = Experiment(
        config=experiment_config,
        save_dir="./experiments/basic_social_sim"
    )

    # Get registry for function registration
    register = exp.register

    # Register environment FoV functions
    @register.env.fov(desc="See other agents within viewing distance")
    async def see_nearby_agents(env, agent):
        """FoV function to see nearby agents."""
        if hasattr(env, 'get_agents_within_radius'):
            # Spatial environment - get agents within radius
            nearby_agents = env.get_agents_within_radius(agent, 15, env.get_agents_in_environment([agent]))
            if nearby_agents:
                descriptions = []
                for other_agent in nearby_agents:
                    distance = ((env.get_agent_position(agent)[0] - env.get_agent_position(other_agent)[0])**2 +
                              (env.get_agent_position(agent)[1] - env.get_agent_position(other_agent)[1])**2)**0.5
                    descriptions.append(f"You see {other_agent.id} about {distance:.1f} meters away")
                return descriptions
        return ["The area appears to be empty"]

    @register.env.fov(desc="Observe the general environment")
    async def observe_environment(env, agent):
        """FoV function to observe environment state."""
        state = env.state
        observations: list[str] = [
            f"The time is {state.get('time_of_day', 'unknown')}",
            f"The weather is {state.get('weather', 'unknown')}",
            f"You are in {env.name}"
        ]

        if state.get('events'):
            observations.append(f"Recent events: {', '.join(state['events'])}")

        return observations

    # Register environment rules
    @register.env.rule(desc="Advance time in the simulation")
    async def advance_time(env, context):
        """Rule to advance time in the environment."""
        current_time = env.state.get('time_of_day', 'morning')

        time_progression = {
            'morning': 'afternoon',
            'afternoon': 'evening',
            'evening': 'night',
            'night': 'morning'
        }

        new_time = time_progression.get(current_time, 'morning')
        env.apply_patch('time_of_day', 'set', new_time)

        # Add time change event
        if 'events' not in env.state:
            env.state['events'] = []
        env.state['events'].append(f"Time changed to {new_time}")

        logger.info(f"Time advanced from {current_time} to {new_time}")

    @register.env.rule(desc="Simulate weather changes")
    async def weather_change(env, context):
        """Rule for weather changes."""
        import random

        current_weather = env.state.get('weather', 'sunny')
        weather_options = ['sunny', 'cloudy', 'rainy', 'windy']

        # 20% chance of weather change
        if random.random() < 0.2:
            new_weather = random.choice([w for w in weather_options if w != current_weather])
            env.apply_patch('weather', 'set', new_weather)

            if 'events' not in env.state:
                env.state['events'] = []
            env.state['events'].append(f"Weather changed to {new_weather}")

            logger.info(f"Weather changed from {current_weather} to {new_weather}")

    # Register environment empowerment functions
    @register.env.empower(desc="Enable agents to move in spatial environment")
    async def spatial_move(env, agent, action_name, direction=None, distance=5.0):
        """Empower agents to move spatially."""
        if not hasattr(env, 'set_agent_position'):
            return "Movement not supported in this environment"

        current_pos = env.get_agent_position(agent)

        if direction:
            import math

            # Convert direction to movement vector
            direction_vectors = {
                'north': (0, 1),
                'south': (0, -1),
                'east': (1, 0),
                'west': (-1, 0),
                'northeast': (0.707, 0.707),
                'northwest': (-0.707, 0.707),
                'southeast': (0.707, -0.707),
                'southwest': (-0.707, -0.707)
            }

            if direction.lower() in direction_vectors:
                dx, dy = direction_vectors[direction.lower()]
                new_x = current_pos[0] + dx * distance
                new_y = current_pos[1] + dy * distance

                env.set_agent_position(agent, new_x, new_y)

                return f"Moved {distance} units {direction} to position ({new_x:.1f}, {new_y:.1f})"
            else:
                return f"Unknown direction: {direction}"
        else:
            return "No direction specified for movement"

    @register.env.empower(desc="Enable social interaction between agents")
    async def social_interact(env, agent, action_name, target_agent_id=None, interaction_type="greeting"):
        """Empower agents to interact socially."""
        if not target_agent_id:
            return "No target agent specified for interaction"

        # Find target agent
        all_agents = env.get_agents_in_environment([agent])  # This is a simplified implementation
        target_agent = None

        # In a real implementation, you'd get all agents from the simulation
        # For now, we'll simulate the interaction

        # Update agent's social connections
        if 'social_connections' not in agent.state:
            agent.state['social_connections'] = []

        if target_agent_id not in agent.state['social_connections']:
            agent.state['social_connections'].append(target_agent_id)

        # Add interaction event
        if 'events' not in env.state:
            env.state['events'] = []
        env.state['events'].append(f"{agent.id} had a {interaction_type} with {target_agent_id}")

        return f"Successfully {interaction_type} with {target_agent_id}"

    # Register agent rules
    @register.agent.rule(desc="Morning preparation routine")
    async def morning_preparation(agent, context):
        """Rule for agent morning preparation."""
        agent.apply_patch('energy', 'set', 100)
        agent.apply_patch('mood', 'set', 'refreshed')

        logger.info(f"Agent {agent.id} completed morning preparation")

    @register.agent.rule(desc="Execute daily routine for rule-based agents")
    async def execute_routine(agent, context):
        """Rule for executing daily routine."""
        if 'routine' in agent.state:
            for activity in agent.state['routine']:
                # Simulate activity execution
                logger.info(f"Agent {agent.id} is doing: {activity}")

                # Update energy based on activity
                if activity == 'work':
                    agent.apply_patch('energy', 'add', -10)
                elif activity == 'walk':
                    agent.apply_patch('energy', 'add', -5)
                elif activity == 'greet':
                    agent.apply_patch('mood', 'set', 'sociable')

    @register.agent.rule(desc="Daily routine for rule-based agents")
    async def daily_routine(agent, context):
        """Execute daily routine based on time of day."""
        # This would be called automatically for rule-based agents
        await execute_routine(agent, context)

    # Register schedule converters
    @register.sched.converter(desc="Extract social interaction outcomes")
    async def extract_social_outcomes(operator_results, input_params):
        """Custom converter to extract social outcomes."""
        social_outcomes = []

        for result in operator_results:
            if 'agent_results' in result:
                for agent_result in result['agent_results']:
                    if agent_result.get('status') == 'success':
                        social_outcomes.append({
                            'agent_id': agent_result['agent_id'],
                            'interaction_description': agent_result.get('result', ''),
                            'timestamp': context.get('time_period', 'unknown')
                        })

        return {
            'social_interactions': social_outcomes,
            'interaction_count': len(social_outcomes),
            'interaction_type': input_params.get('interaction_type', 'general')
        }

    # Validate configuration
    logger.info("Validating experiment configuration...")
    validation_issues = await exp.validate_configuration()
    if validation_issues:
        logger.warning("Configuration issues found:")
        for issue in validation_issues:
            logger.warning(f"  - {issue}")
    else:
        logger.info("Configuration validated successfully")

    # Run the simulation
    logger.info("Starting simulation...")
    try:
        await exp.run(steps=3)
        logger.info("Simulation completed successfully!")

        # Print experiment info
        info = exp.get_experiment_info()
        logger.info(f"Final experiment state:")
        logger.info(f"  - Total agents: {info['total_agents']}")
        logger.info(f"  - Environment type: {info['environment_type']}")
        logger.info(f"  - Completed steps: {info['current_step']}")
        logger.info(f"  - Save directory: {info['save_dir']}")

    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        raise


if __name__ == "__main__":
    # Run the example
    asyncio.run(main())