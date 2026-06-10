# Society0 Simulation Engine

A data-driven, LLM-empowered social simulation framework implementing the Society0 design philosophy.

## Overview

The Society0 simulation engine provides a complete framework for creating complex social simulations with LLM-driven agents. It features a dual-capability agent model, environment-mediated interactions, and comprehensive state management with checkpoint/recovery capabilities.

## Key Features

- **Data-Driven Configuration**: Define simulations through YAML config files and function decorators
- **Mixed Agent Types**: Support for both LLM-driven and rule-based agents
- **Four-Phase DAG Execution**: Input → Select → Operate → Convert pipeline for each step
- **Environment Empowerment**: Agents gain capabilities through environment interaction
- **Complete State Management**: Checkpoint creation, restoration, and parallel world branching
- **AgentBubu Integration**: ReAct capabilities for LLM agents
- **Spatial Awareness**: Built-in spatial environment with position-based interactions

## Quick Start

```python
import asyncio
from libs.simengine import Experiment

# Create experiment with config
exp = Experiment(config="./config.yaml", save_dir="./my_experiment")
register = exp.register

# Register environment field of view
@register.env.fov(desc="See nearby agents")
async def see_nearby(env, agent):
    return ["You see other agents nearby..."]

# Register agent rules
@register.agent.rule(desc="Daily routine")
async def daily_routine(agent, context):
    agent.apply_patch('energy', 'add', -10)

# Register schedule operations
@register.sched.operator(desc="Social interaction")
async def social_interact(agents, env, input_params):
    # Implementation here
    return {"interaction_results": "..."}

# Run simulation
await exp.run(steps=10)
```

## Configuration Structure

```yaml
experiment:
  name: "my_simulation"

environment:
  type: "spatial"
  state:
    time_of_day: "morning"
  rules: ["advance_time"]
  fovs: ["see_nearby_agents"]
  empower_actions:
    move: "spatial_move"

agents:
  - id: "alice"
    type: "llm"
    persona: "A curious researcher"
    state: {mood: "curious"}

  - id: "bob"
    type: "rule"
    state: {energy: 100}
    rules: ["daily_routine"]

schedule:
  nodes:
    - id: "morning_setup"
      selector: {type: "all_agents"}
      operators: [{type: "rule", rule_name: "morning_prep"}]
      converter: {type: "summary"}
```

## Architecture Components

### Core Classes

- **`Experiment`**: Main user interface for simulation setup and execution
- **`StepNode`**: Four-phase execution unit (Input→Select→Operate→Convert)
- **`BaseAgent`**: Agent base class with state management
- **`BaseEnvironment`**: Environment with FoV, rules, and empowerment
- **`StateManager`**: Checkpoint creation and restoration

### Agent Types

- **`LLMAgent`**: AgentBubu-powered agents with ReAct capabilities
- **`RuleAgent`**: Deterministic/stochastic rule-driven agents

### Execution Components

- **Selectors**: Choose which agents to operate on
- **Operators**: Execute actions on selected agents
- **Converters**: Transform results for downstream consumption

## Function Registration

The engine uses a decorator-based registration system:

```python
# Environment functions
@register.env.fov(desc="Field of view function")
async def see_agents(env, agent): pass

@register.env.rule(desc="Environment rule")
async def update_weather(env, context): pass

@register.env.empower(desc="Action empowerment")
async def enable_movement(env, agent, action, **params): pass

# Agent functions
@register.agent.rule(desc="Agent rule")
async def agent_routine(agent, context): pass

@register.agent.action(desc="Agent action")
async def perform_task(agent, env, **params): pass

# Schedule functions
@register.sched.selector(desc="Agent selector")
async def select_by_mood(env, agents, input_params): pass

@register.sched.operator(desc="Operation on agents")
async def instruct_agents(agents, env, input_params): pass

@register.sched.converter(desc="Result converter")
async def summarize_results(operator_results, input_params): pass
```

## State Management

The engine provides comprehensive state management:

```python
# Run simulation with automatic checkpointing
await exp.run(steps=10)

# Resume from checkpoint
await exp.resume(from_step=5)

# Create parallel world for experimentation
parallel_exp = await exp.create_parallel_world(from_step=3, branch_name="experiment_a")
await parallel_exp.run(steps=5)
```

## Integration with Existing Libraries

- **dag_engine**: Used for DAG execution and dependency resolution
- **AgentBubu**: Powers LLM agents with ReAct capabilities and tool integration
- **PocketFlow**: Available for micro-orchestration within operators

## Example Applications

The engine supports various simulation scenarios:

- Economic modeling with markets and traders
- Social network dynamics and information spread
- Urban planning with spatial agent interactions
- Political opinion formation and polarization
- Organizational behavior and decision-making

## File Structure

```
libs/simengine/
├── core/
│   ├── experiment.py        # Main Experiment class
│   ├── step_node.py         # Four-phase execution
│   └── registry.py          # Function registration
├── components/
│   ├── agent.py             # Agent implementations
│   ├── environment.py       # Environment implementations
│   └── state_manager.py     # State persistence
├── execution/
│   ├── selectors.py         # Agent selection logic
│   ├── operators.py         # Operation implementations
│   └── converters.py        # Result transformation
└── integration/
    ├── dag_integration.py   # DAG engine integration
    └── bubu_integration.py  # AgentBubu integration
```

## Design Principles

1. **Data-Driven**: Everything configurable through files and decorators
2. **State Isolation**: Components manage their own state via apply_patch()
3. **Complete Persistence**: Any step can be checkpointed and restored
4. **Environment Mediation**: All agent capabilities flow through environment
5. **Parallel Execution**: DAG-based execution with dependency resolution

This engine implements the complete Society0 design philosophy while remaining practical and extensible for real-world simulation needs.