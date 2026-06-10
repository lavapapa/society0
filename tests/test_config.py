"""
Test configuration for SimEngine V2.
Clean, simple configuration following the new architecture.
"""

test_config = {
    "experiment": {
        "name": "v2_basic_test",
        "description": "Testing the V2 architecture with basic agents and interactions"
    },
    
    "globals": {
        "simulation_time": 0,
        "world_events": []
    },
    
    "environment": {
        "type": "basic",
        "state": {
            "temperature": 20.0,
            "weather": "sunny",
            "resources": {"food": 1000, "water": 1000}
        }
    },
    
    "agents": [
        {
            "id": "alice",
            "type": "user",           # Social role: regular user
            "archetype": "llm",       # Execution mechanism: LLM-driven
            "state": {
                "energy": 100,
                "mood": "curious",
                "inventory": {},
                "memory": []
            }
        },
        {
            "id": "bob", 
            "type": "worker",         # Social role: worker
            "archetype": "rule",      # Execution mechanism: rule-based
            "state": {
                "energy": 90,
                "mood": "practical", 
                "inventory": {},
                "task_queue": ["gather_resources", "build_shelter"]
            }
        },
        {
            "id": "charlie", 
            "type": "social_coordinator",  # Social role: coordinator
            "archetype": "rule",           # Execution mechanism: rule-based
            "state": {
                "energy": 80,
                "mood": "social",
                "inventory": {},
                "friends": []
            }
        }
    ],
    
    "schedule": {
        "nodes": [
            {
                "id": "morning_setup",
                "selector": {
                    "type": "all_agents"
                },
                "operators": [  # Changed to list to support multiple operators
                    {
                        "type": "rule",
                        "rule_name": "morning_routine",
                        "target": "agents"
                    }
                ],
                "converter": {
                    "type": "summary"
                },
                "dependencies": []
            },
            
            {
                "id": "llm_thinking",
                "inputs": {
                    "weather": {
                        "type": "node_output",
                        "node_id": "morning_setup",
                        "output_key": "weather",
                        "default": "unknown"
                    }
                },
                "selector": {
                    "type": "by_type",
                    "agent_type": "llm"
                },
                "operators": [
                    {
                        "type": "instruct", 
                        "instruction": "Look around and decide what you want to do today. The weather is {weather}. Consider your energy level and mood."
                    }
                ],
                "converter": {
                    "type": "passthrough"
                },
                "dependencies": ["morning_setup"]
            },
            
            {
                "id": "rule_actions",
                "selector": {
                    "type": "by_type",
                    "agent_type": "rule"
                },
                "operators": [
                    {
                        "type": "rule",
                        "rule_name": "execute_daily_tasks",
                        "target": "agents"
                    }
                ],
                "converter": {
                    "type": "summary"
                },
                "dependencies": ["morning_setup"]
            },
            
            {
                "id": "environment_update",
                "selector": {
                    "type": "all_agents"
                },
                "operators": [
                    {
                        "type": "rule",
                        "rule_name": "advance_world_time", 
                        "target": "environment"
                    }
                ],
                "converter": {
                    "type": "passthrough"
                },
                "dependencies": ["llm_thinking", "rule_actions"]
            }
        ]
    }
}