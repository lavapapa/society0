"""
Example configuration and usage of the Society0 simulation engine.
"""

experiment_config = {
    "experiment": {
        "name": "basic_social_simulation",
        "description": "A basic example of social interaction simulation"
    },
    
    "environment": {
        "type": "spatial",
        "name": "town_square",
        "width": 100,
        "height": 100,
        "state": {
            "time_of_day": "morning",
            "weather": "sunny",
            "events": []
        },
        "rules": ["advance_time", "weather_change"],
        "fovs": ["see_nearby_agents", "observe_environment"],
        "empower_actions": {
            "move": "spatial_move",
            "interact": "social_interact",
            "observe": "enhanced_observe"
        }
    },
    
    "agents": [
        {
            "id": "alice",
            "type": "llm",
            "persona": "A curious and friendly researcher who enjoys meeting new people and learning about their perspectives.",
            "model": "claude-3-5-sonnet-20241022",
            "state": {
                "mood": "curious",
                "energy": 100,
                "social_connections": [],
                "knowledge_gained": []
            }
        },
        {
            "id": "bob", 
            "type": "llm",
            "persona": "A practical engineer who prefers action over words but is always willing to help others.",
            "model": "claude-3-5-sonnet-20241022", 
            "state": {
                "mood": "focused",
                "energy": 90,
                "social_connections": [],
                "projects": ["bridge_design"]
            }
        },
        {
            "id": "charlie",
            "type": "rule",
            "state": {
                "mood": "cheerful",
                "energy": 80,
                "routine": ["walk", "greet", "work"]
            },
            "rules": ["daily_routine"],
            "default_rules": ["daily_routine"]
        }
    ],
    
    "schedule": {
        "nodes": [
            {
                "id": "morning_setup",
                "inputs": {
                    "time_period": {"type": "value", "value": "morning"}
                },
                "selector": {
                    "type": "all_agents"
                },
                "operators": [
                    {
                        "type": "rule",
                        "rule_name": "morning_preparation",
                        "target": "agents"
                    }
                ],
                "converter": {
                    "type": "summary"
                }
            },
            
            {
                "id": "social_interaction",
                "dependencies": ["morning_setup"],
                "inputs": {
                    "interaction_type": {"type": "value", "value": "casual_meeting"}
                },
                "selector": {
                    "type": "by_type",
                    "agent_type": "llm"
                },
                "operators": [
                    {
                        "type": "instruct",
                        "instruction": "Look around and decide how you want to interact with other people in the town square. Consider your personality and current mood."
                    }
                ],
                "converter": {
                    "type": "custom",
                    "function": "extract_social_outcomes"
                }
            },
            
            {
                "id": "rule_based_actions",
                "dependencies": ["morning_setup"],
                "inputs": {},
                "selector": {
                    "type": "by_type", 
                    "agent_type": "rule"
                },
                "operators": [
                    {
                        "type": "rule",
                        "rule_name": "execute_routine",
                        "target": "agents"
                    }
                ],
                "converter": {
                    "type": "passthrough"
                }
            },
            
            {
                "id": "environment_update",
                "dependencies": ["social_interaction", "rule_based_actions"],
                "inputs": {},
                "selector": {
                    "type": "all_agents"
                },
                "operators": [
                    {
                        "type": "rule",
                        "rule_name": "time_progression",
                        "target": "environment"
                    }
                ],
                "converter": {
                    "type": "summary"
                }
            }
        ]
    }
}