"""
SimEngine V2: Schedule - The centralized brain of the simulation.

Schedule compiles configuration into executable plans and drives DAG execution.
It's the single point of control for all simulation behavior.
"""

from typing import Dict, Any, List, Protocol, Callable, Optional, Union
import asyncio
import logging
import time
from dataclasses import dataclass, field

from core_data import WorldState, StatePatch, ExecutionContext, Agent
from .async_utils import invoke_maybe_async

logger = logging.getLogger(__name__)


# Protocol definitions for Selectors, Operators, and Converters with ExecutionContext
class Selector(Protocol):
    """Protocol for selector functions - now supports generalized entity selection."""
    async def __call__(self, params: Dict[str, Any], context: ExecutionContext) -> List[Union[str, Agent]]:
        """Select entities based on parameters and context.
        
        Returns:
            List of agent IDs (str) or Agent objects, or special entity identifiers like 'environment'
        """
        ...


class Operator(Protocol):
    """Protocol for operator functions - uses ExecutionContext for standardized interface."""
    async def __call__(self, targets: List[Union[str, Agent]], params: Dict[str, Any], context: ExecutionContext) -> Dict[str, Any]:
        """Execute operation on selected targets, directly modify world_state via context.
        
        Args:
            targets: Selected entities (agent IDs, Agent objects, or special identifiers)
            params: Static parameters from configuration
            context: Standardized execution context
            
        Returns:
            Execution information and results
        """
        ...


class Converter(Protocol):
    """Protocol for converter functions - uses ExecutionContext."""
    async def __call__(self, operator_results: List[Dict[str, Any]], params: Dict[str, Any], context: ExecutionContext) -> Dict[str, Any]:
        """Convert operator results into output for step context.
        
        Args:
            operator_results: Results from all operators in this node
            params: Static parameters from configuration
            context: Standardized execution context
            
        Returns:
            Converted output data
        """
        ...


@dataclass
class StepNode:
    """Compiled step node for execution with full data flow support."""
    id: str
    selector_func: Selector
    operator_funcs: List[Operator]          # Changed to support multiple operators
    converter_func: Optional[Converter]     # Added converter support
    inputs_config: Dict[str, Any]           # Added inputs configuration
    selector_params: Dict[str, Any]
    operators_params: List[Dict[str, Any]]  # Changed to list for multiple operators
    converter_params: Dict[str, Any]        # Added converter parameters
    dependencies: List[str]
    inputs: Dict[str, Any] = field(default_factory=dict)  # Runtime inputs from upstream nodes


class StepFlow:
    """Single-step flow execution engine.
    
    Responsibilities:
    - Hold a compiled DAG for a single simulation step
    - Execute the DAG with proper dependency resolution
    - Manage step-local context and data flow
    """
    
    def __init__(self, step_number: int, step_config: dict, function_registry: 'FunctionRegistry'):
        """Initialize StepFlow with step-specific configuration.
        
        Args:
            step_number: The step number this flow represents
            step_config: Configuration for this specific step
            function_registry: Registry containing all registered functions
        """
        self.step_number = step_number
        self.config = step_config
        self.registry = function_registry
        self.step_nodes: List[StepNode] = []
        self.dependencies: Dict[str, List[str]] = {}
        self.step_context: Dict[str, Any] = {}
        
        # Compile step configuration into executable nodes
        self._compile_step()
    
    def _compile_step(self) -> None:
        """Compile step configuration into executable StepNodes."""
        nodes_config = self.config.get('nodes', [])
        
        for node_config in nodes_config:
            try:
                step_node = self._compile_step_node(node_config)
                self.step_nodes.append(step_node)
                
                # Build dependency graph
                node_id = node_config['id']
                deps = node_config.get('dependencies', [])
                self.dependencies[node_id] = deps
                
            except Exception as e:
                logger.error(f"Failed to compile step node {node_config.get('id', 'unknown')}: {e}")
                raise
        
        logger.debug(f"Compiled step {self.step_number} with {len(self.step_nodes)} nodes")
    
    async def execute(self, world_state: WorldState, context: ExecutionContext) -> Dict[str, Any]:
        """Execute this step's DAG, directly modify world_state, return execution metrics.
        
        Args:
            world_state: Current world state to modify directly
            context: Execution context from parent Schedule
            
        Returns:
            Execution metrics and results for this step
        """
        logger.debug(f"Executing step {self.step_number} with {len(self.step_nodes)} nodes")
        
        # Reset step context
        self.step_context = {}
        start_time = time.time()
        
        try:
            # Use DAG execution with proper dependency resolution
            execution_results = await self._execute_dag_nodes(world_state, context)
            
        except ImportError:
            logger.warning("DAG engine not available, falling back to sequential execution")
            # Fallback to sequential execution
            execution_results = await self._execute_sequential(world_state, context)
        
        execution_time = time.time() - start_time
        
        return {
            "step_number": self.step_number,
            "nodes_executed": len(self.step_nodes),
            "execution_time": execution_time,
            "step_context": self.step_context.copy(),
            "results": execution_results
        }
    
    # Move compilation methods to StepFlow
    def _compile_step_node(self, node_config: Dict[str, Any]) -> StepNode:
        """Compile a single step node configuration with full data flow support."""
        # This method will be implemented by moving from the original Schedule class
        pass  # TODO: Implement compilation logic


class Schedule:
    """The centralized brain of the simulation with full data flow support.
    
    Now acts as a plan manager that holds StepFlow instances.
    Compiles configuration into executable plans and drives execution with step context.
    """
    
    def __init__(self, schedule_config: Dict[str, Any], function_registry: 'FunctionRegistry'):
        """
        Compile schedule configuration into executable plan.
        
        Args:
            schedule_config: Configuration dictionary for the schedule
            function_registry: Registry containing all registered functions
        """
        self.config = schedule_config
        self.registry = function_registry
        self.step_nodes: List[StepNode] = []
        self.dependencies: Dict[str, List[str]] = {}
        self.step_context: Dict[str, Any] = {}  # Added for data flow between nodes
        
        # Compile configuration into executable plan
        self._compile_schedule()
    
    def _compile_schedule(self) -> None:
        """Compile the schedule configuration into executable StepNodes."""
        nodes_config = self.config.get('nodes', [])
        
        for node_config in nodes_config:
            try:
                step_node = self._compile_step_node(node_config)
                self.step_nodes.append(step_node)
                
                # Build dependency graph
                node_id = node_config['id']
                deps = node_config.get('dependencies', [])
                self.dependencies[node_id] = deps
                
            except Exception as e:
                logger.error(f"Failed to compile step node {node_config.get('id', 'unknown')}: {e}")
                raise
        
        logger.info(f"Compiled schedule with {len(self.step_nodes)} nodes")
    
    def _compile_step_node(self, node_config: Dict[str, Any]) -> StepNode:
        """Compile a single step node configuration with full data flow support."""
        node_id = node_config['id']
        
        # Compile selector
        selector_config = node_config.get('selector', {})
        selector_func, selector_params = self._compile_selector(selector_config)
        
        # Compile multiple operators 
        operators_config = node_config.get('operators', [])  # Now expects a list
        if not operators_config:
            # Backward compatibility: check for single 'operator' field
            single_operator = node_config.get('operator')
            if single_operator:
                operators_config = [single_operator]
        
        operator_funcs = []
        operators_params = []
        for op_config in operators_config:
            op_func, op_params = self._compile_operator(op_config)
            operator_funcs.append(op_func)
            operators_params.append(op_params)
        
        # Compile converter
        converter_config = node_config.get('converter', {})
        converter_func, converter_params = self._compile_converter(converter_config)
        
        # Get inputs configuration
        inputs_config = node_config.get('inputs', {})
        
        # Get dependencies
        dependencies = node_config.get('dependencies', [])
        
        return StepNode(
            id=node_id,
            selector_func=selector_func,
            operator_funcs=operator_funcs,
            converter_func=converter_func,
            inputs_config=inputs_config,
            selector_params=selector_params,
            operators_params=operators_params,
            converter_params=converter_params,
            dependencies=dependencies
        )
    
    def _compile_selector(self, selector_config: Dict[str, Any]) -> tuple[Selector, Dict[str, Any]]:
        """Compile selector configuration into callable function."""
        selector_type = selector_config.get('type', 'all_agents')
        params = {k: v for k, v in selector_config.items() if k != 'type'}
        
        # Built-in selectors
        if selector_type == 'all_agents':
            return self._all_agents_selector, params
        elif selector_type == 'by_type':
            return self._by_type_selector, params
        elif selector_type == 'by_id':
            return self._by_id_selector, params
        elif selector_type == 'custom':
            # Custom registered selector
            func_name = selector_config.get('function')
            if func_name and func_name in self.registry.selectors:
                selector_func = self.registry.selectors[func_name]['function']
                return selector_func, params
            else:
                raise ValueError(f"Custom selector function '{func_name}' not found")
        else:
            raise ValueError(f"Unknown selector type: {selector_type}")
    
    def _compile_operator(self, operator_config: Dict[str, Any]) -> tuple[Operator, Dict[str, Any]]:
        """Compile operator configuration into callable function."""
        operator_type = operator_config.get('type', 'custom')
        params = {k: v for k, v in operator_config.items() if k != 'type'}
        
        # Built-in operators
        if operator_type == 'instruct':
            return self._instruct_operator, params
        elif operator_type == 'rule':
            return self._rule_operator, params
        elif operator_type == 'custom':
            # Custom registered operator
            func_name = operator_config.get('function')
            if func_name and func_name in self.registry.operators:
                operator_func = self.registry.operators[func_name]['function']
                return operator_func, params
            else:
                raise ValueError(f"Custom operator function '{func_name}' not found")
        else:
            raise ValueError(f"Unknown operator type: {operator_type}")
    
    def _compile_converter(self, converter_config: Dict[str, Any]) -> tuple[Optional[Converter], Dict[str, Any]]:
        """Compile converter configuration into callable function."""
        if not converter_config:
            return None, {}
        
        converter_type = converter_config.get('type', 'passthrough')
        params = {k: v for k, v in converter_config.items() if k != 'type'}
        
        # Built-in converters
        if converter_type == 'passthrough':
            return self._passthrough_converter, params
        elif converter_type == 'summary':
            return self._summary_converter, params
        elif converter_type == 'custom':
            # Custom registered converter
            func_name = converter_config.get('function')
            if func_name and func_name in self.registry.converters:
                converter_func = self.registry.converters[func_name]['function']
                return converter_func, params
            else:
                raise ValueError(f"Custom converter function '{func_name}' not found")
        else:
            raise ValueError(f"Unknown converter type: {converter_type}")
    
    # Built-in converter implementations
    async def _passthrough_converter(self, operator_results: List[Dict[str, Any]], 
                                   params: Dict[str, Any]) -> Dict[str, Any]:
        """Pass through operator results unchanged."""
        return {"operator_results": operator_results}
    
    async def _summary_converter(self, operator_results: List[Dict[str, Any]], 
                               params: Dict[str, Any]) -> Dict[str, Any]:
        """Create summary of operator results."""
        return {
            "total_operations": len(operator_results),
            "operation_summary": f"Executed {len(operator_results)} operations"
        }
    
    # Built-in selector implementations
    async def _all_agents_selector(self, world_state: WorldState, params: Dict[str, Any]) -> List[str]:
        """Select all agents."""
        return list(world_state.agents.keys())
    
    async def _by_type_selector(self, world_state: WorldState, params: Dict[str, Any]) -> List[str]:
        """Select agents by type."""
        agent_type = params.get('agent_type')
        if not agent_type:
            logger.warning("No agent_type specified for by_type selector")
            return []
        
        return [agent_id for agent_id, agent in world_state.agents.items() 
                if agent.type == agent_type]
    
    async def _by_id_selector(self, world_state: WorldState, params: Dict[str, Any]) -> List[str]:
        """Select agents by specific IDs."""
        agent_ids = params.get('agent_ids', [])
        return [agent_id for agent_id in agent_ids if agent_id in world_state.agents]
    
    # Built-in operator implementations - now directly modify world_state
    async def _instruct_operator(self, agent_ids: List[str], world_state: WorldState, 
                                params: Dict[str, Any]) -> Dict[str, Any]:
        """Instruct LLM agents - directly modify world_state."""
        instruction = params.get('instruction', '')
        if not instruction:
            logger.warning("No instruction provided for instruct operator")
            return {"error": "No instruction provided", "agent_count": 0}
        
        # Template rendering support
        instruction = self._render_template(instruction, params)
        
        results = []
        successful_count = 0
        
        for agent_id in agent_ids:
            agent = world_state.get_agent(agent_id)
            if not agent:
                continue
            
            if agent.type == 'llm':
                try:
                    # For now, simulate LLM instruction processing
                    # In real implementation, this would call AgentBubu integration
                    result = f"Agent {agent_id} processed instruction: {instruction}"
                    
                    # Directly modify agent's state
                    if 'last_instruction_result' not in agent.state:
                        agent.state['last_instruction_result'] = result
                    else:
                        agent.state['last_instruction_result'] = result
                    
                    results.append({"agent_id": agent_id, "result": result})
                    successful_count += 1
                    
                except Exception as e:
                    logger.error(f"Instruct failed for agent {agent_id}: {e}")
                    results.append({"agent_id": agent_id, "error": str(e)})
        
        return {
            "operation": "instruct",
            "instruction": instruction,
            "results": results,
            "successful_count": successful_count
        }
    
    async def _rule_operator(self, agent_ids: List[str], world_state: WorldState,
                           params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute rules on agents or environment - directly modify world_state."""
        rule_name = params.get('rule_name')
        target = params.get('target', 'agents')
        
        if not rule_name:
            logger.warning("No rule_name specified for rule operator")
            return {"error": "No rule_name specified"}
        
        results = []
        successful_count = 0
        
        if target == 'agents':
            # Execute agent rule - the rule function should directly modify world_state/agents
            if rule_name in self.registry.agent_rules:
                rule_func = self.registry.agent_rules[rule_name]['function']
                
                for agent_id in agent_ids:
                    agent = world_state.get_agent(agent_id)
                    if agent:
                        try:
                            # Call rule function - it now directly modifies agent/world_state
                            await invoke_maybe_async(rule_func, agent, world_state, params)
                            results.append({"agent_id": agent_id, "status": "success"})
                            successful_count += 1
                        except Exception as e:
                            logger.error(f"Agent rule {rule_name} failed for {agent_id}: {e}")
                            results.append({"agent_id": agent_id, "error": str(e)})
        
        elif target == 'environment':
            # Execute environment rule - the rule function should directly modify world_state.environment
            if rule_name in self.registry.env_rules:
                rule_func = self.registry.env_rules[rule_name]['function']
                
                try:
                    await invoke_maybe_async(rule_func, world_state.environment, world_state, params)
                    results.append({"target": "environment", "status": "success"})
                    successful_count = 1
                except Exception as e:
                    logger.error(f"Environment rule {rule_name} failed: {e}")
                    results.append({"target": "environment", "error": str(e)})
        
        return {
            "operation": "rule",
            "rule_name": rule_name,
            "target": target,
            "results": results,
            "successful_count": successful_count
        }
    
    def _render_template(self, template: str, params: Dict[str, Any]) -> str:
        """Render template string with parameters."""
        try:
            return template.format(**params)
        except KeyError as e:
            logger.warning(f"Template rendering failed: {e}")
            return template
    
    async def execute_step(self, world_state: WorldState) -> None:
        """
        Execute one complete simulation step - directly modifies world_state.
        
        Args:
            world_state: Current world state to modify directly
        """
        logger.info(f"Executing step {world_state.step} with {len(self.step_nodes)} nodes")
        
        # Reset step context for this step
        self.step_context = {}
        
        try:
            # Use DAG execution with proper dependency resolution
            await self._execute_dag_nodes(world_state)
            
        except ImportError:
            logger.warning("DAG engine not available, falling back to sequential execution")
            # Fallback to sequential execution
            await self._execute_sequential(world_state)
        
        logger.info(f"Step execution completed")
    
    async def _execute_dag_nodes(self, world_state: WorldState) -> None:
        """Execute nodes using DAG engine."""
        from libs.dag_engine import DAGRunner
        dag_runner = DAGRunner()
        
        # Create task functions for each step node
        tasks = {}
        for step_node in self.step_nodes:
            tasks[step_node.id] = self._create_node_task(step_node, world_state)
        
        # Execute DAG
        results = await dag_runner.run(tasks=tasks, dependencies=self.dependencies)
        
        # Results are already applied to world_state through direct modification
        logger.debug(f"DAG execution completed with {len(results)} nodes")
    
    def _create_node_task(self, step_node: StepNode, world_state: WorldState) -> Callable:
        """Create an async task function for a step node with full data flow."""
        async def node_task(context: Dict[str, Any] = None) -> Dict[str, Any]:
            try:
                # Phase 1: Input Resolution - prepare parameters from step context
                resolved_params = self._resolve_inputs(step_node.inputs_config, self.step_context)
                
                # Phase 2: Select agents  
                selected_agent_ids = await step_node.selector_func(
                    world_state, {**step_node.selector_params, **resolved_params})
                
                # Phase 3: Execute all operators in sequence
                operator_results = []
                for i, (op_func, op_params) in enumerate(zip(step_node.operator_funcs, step_node.operators_params)):
                    # Merge resolved params with operator-specific params
                    combined_params = {**op_params, **resolved_params}
                    
                    # Execute operator - it directly modifies world_state
                    op_result = await op_func(selected_agent_ids, world_state, combined_params)
                    operator_results.append(op_result)
                
                # Phase 4: Convert results if converter exists
                node_output = {}
                if step_node.converter_func:
                    node_output = await step_node.converter_func(
                        operator_results, {**step_node.converter_params, **resolved_params})
                
                # Update step context with node output for downstream nodes
                self.step_context[step_node.id] = node_output
                
                logger.debug(f"Node {step_node.id}: selected {len(selected_agent_ids)} agents, {len(operator_results)} operations")
                return node_output
                
            except Exception as e:
                logger.error(f"Node {step_node.id} execution failed: {e}")
                # Still update context to avoid blocking downstream nodes
                self.step_context[step_node.id] = {"error": str(e)}
                return {"error": str(e)}
        
        return node_task
    
    def _resolve_inputs(self, inputs_config: Dict[str, Any], step_context: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve input parameters from step context."""
        resolved = {}
        
        for input_name, input_spec in inputs_config.items():
            if isinstance(input_spec, str):
                # Simple reference to step context key
                if input_spec in step_context:
                    resolved[input_name] = step_context[input_spec]
                else:
                    logger.debug(f"Input '{input_spec}' not found in step context for input {input_name}")
                    resolved[input_name] = None
            
            elif isinstance(input_spec, dict):
                # Complex input specification
                input_type = input_spec.get("type", "value")
                
                if input_type == "value":
                    resolved[input_name] = input_spec.get("value")
                elif input_type == "context":
                    context_key = input_spec.get("key")
                    if context_key and context_key in step_context:
                        resolved[input_name] = step_context[context_key]
                    else:
                        resolved[input_name] = input_spec.get("default")
                elif input_type == "node_output":
                    node_id = input_spec.get("node_id")
                    output_key = input_spec.get("output_key", "output")
                    
                    if node_id in step_context and output_key in step_context[node_id]:
                        resolved[input_name] = step_context[node_id][output_key]
                    else:
                        resolved[input_name] = input_spec.get("default")
                else:
                    resolved[input_name] = input_spec.get("default")
            else:
                # Direct value
                resolved[input_name] = input_spec
        
        return resolved
    
    async def _execute_sequential(self, world_state: WorldState) -> None:
        """Fallback sequential execution when DAG engine is not available."""
        executed = set()
        
        while len(executed) < len(self.step_nodes):
            progress_made = False
            
            for step_node in self.step_nodes:
                if step_node.id in executed:
                    continue
                
                # Check if all dependencies are satisfied
                deps_satisfied = all(dep in executed for dep in step_node.dependencies)
                
                if deps_satisfied:
                    # Execute node
                    try:
                        task_func = self._create_node_task(step_node, world_state)
                        await task_func()
                        
                        executed.add(step_node.id)
                        progress_made = True
                        
                        logger.debug(f"Sequential execution: {step_node.id} completed")
                        
                    except Exception as e:
                        logger.error(f"Sequential execution failed for {step_node.id}: {e}")
                        executed.add(step_node.id)  # Mark as executed to avoid infinite loop
                        progress_made = True
            
            if not progress_made:
                logger.error("Sequential execution stuck - possible circular dependencies")
                break
