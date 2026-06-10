"""
SimEngine V2: Schedule - The centralized brain of the simulation.

This module implements the new layered scheduling architecture:
- Schedule: Plan manager that holds StepFlow instances
- StepFlow: Single-step execution engine with DAG support
- StepNode: Compiled execution nodes with full data flow support

The architecture follows the comprehensive refactor guide principles.
"""

from typing import Dict, Any, List, Protocol, Callable, Optional, Union, Set, Tuple
import asyncio
import json
import logging
import time
import copy
import inspect
import traceback
from dataclasses import dataclass, field, replace

from .core_data import World, ExecutionContext, BaseOperatorResult, InstructOperatorResult
from .diff_dispatcher import NodeDiffPayload
from .events import StateChangeEvent, BaseEvent
from .jmespath_context import JmespathContextBuilder
from .async_utils import invoke_maybe_async
from .logging import (
    ExperimentLogContext,
    LogField,
    ScheduleEvent,
)

logger = logging.getLogger(__name__)


# Protocol definitions for Selectors, Operators, and Converters with ExecutionContext
class Selector(Protocol):
    """Protocol for selector functions - returns Agent objects directly."""
    async def __call__(self, params: Dict[str, Any], context: ExecutionContext) -> List['Agent']:
        """Select agents based on parameters and context.

        Returns:
            List of Agent objects (not IDs)
        """
        ...


class Operator(Protocol):
    """Protocol for operator functions - returns BaseOperatorResult instances."""
    async def __call__(self, agents: List['Agent'], params: Dict[str, Any], context: ExecutionContext) -> BaseOperatorResult:
        """Execute operation on selected targets, directly modify world_state via context.

        Args:
            agents: Selected agents (Agent objects)
            params: Static parameters from configuration
            context: Standardized execution context

        Returns:
            BaseOperatorResult instance with execution information
        """
        ...


class Converter(Protocol):
    """Protocol for converter functions - processes BaseOperatorResult instances with world access."""
    async def __call__(self, operator_results: List[BaseOperatorResult], params: Dict[str, Any], context: ExecutionContext, world: 'World') -> Dict[str, Any]:
        """Convert operator results into output for step context with world state access.

        Args:
            operator_results: Results from all operators in this node
            params: Static parameters from configuration
            context: Standardized execution context
            world: Complete world state for "god view" access

        Returns:
            Converted output data
        """
        ...


@dataclass
class OperatorConfig:
    """V2: Enhanced operator configuration with id and params mapping support."""
    id: str                               # V2: Operator identifier for result storage
    type: str                            # Operator type
    func: Operator                       # Compiled operator function
    params: Dict[str, Any]               # V2: Enhanced params with JMESPath support
    base_params: Dict[str, Any]          # Original parameters before rendering


@dataclass
class StepNode:
    """Compiled step node for execution with V2 parallel Agent pipeline support."""
    id: str
    selector_func: Selector
    selector_type: str
    operator_configs: List[OperatorConfig]  # V2: Enhanced operator configurations
    converter_func: Optional[Converter]
    converter_type: Optional[str]
    inputs_config: Dict[str, Any]
    selector_params: Dict[str, Any]
    converter_params: Dict[str, Any]
    dependencies: List[str]
    concurrency_limit: int = 200             # V2: Configurable concurrency for parallel Agent mode
    inputs: Dict[str, Any] = field(default_factory=dict)  # Runtime inputs from upstream nodes

    # V2: Legacy compatibility
    @property
    def operator_funcs(self) -> List[Operator]:
        """Legacy compatibility: return operator functions."""
        return [config.func for config in self.operator_configs]

    @property
    def operators_params(self) -> List[Dict[str, Any]]:
        """Legacy compatibility: return operator parameters."""
        return [config.params for config in self.operator_configs]


class StepFlow:
    """Single-step flow execution engine.

    Responsibilities:
    - Hold a compiled DAG for a single simulation step
    - Execute the DAG with proper dependency resolution
    - Manage step-local context and data flow
    """

    def __init__(
        self,
        step_number: int,
        step_config: dict,
        function_registry: 'FunctionRegistry',
        context_builder: Optional[JmespathContextBuilder] = None,
        log_context: Optional[ExperimentLogContext] = None,
    ):
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
        self._context_builder = context_builder or JmespathContextBuilder()
        self._log_context = log_context
        self._render_cache: Dict[Tuple[str, str, str, int, int], Dict[str, Any]] = {}

        # Compile step configuration into executable nodes
        self._compile_step()

    def _log_schedule(self, level: str, event: str, **payload: Any) -> None:
        if not self._log_context:
            return
        record = {str(LogField.STEP): self.step_number, **payload}
        self._log_context.log_schedule(level, event, **record)

    def _compile_step(self) -> None:
        """Compile step configuration into executable StepNodes."""
        nodes_config = self.config.get('nodes', [])
        print(f"🔧 [COMPILE DEBUG] Starting step compilation for step {self.step_number}")
        print(f"🔧 [COMPILE DEBUG] Found {len(nodes_config)} nodes to compile")

        for i, node_config in enumerate(nodes_config):
            print(f"🔧 [COMPILE DEBUG] Compiling node {i+1}/{len(nodes_config)}: {node_config.get('id', 'unknown')}")
            try:
                step_node = self._compile_step_node(node_config)
                self.step_nodes.append(step_node)

                # Build dependency graph
                node_id = node_config['id']
                deps = node_config.get('dependencies', [])
                self.dependencies[node_id] = deps
                print(f"🔧 [COMPILE DEBUG] Node {node_id} compiled successfully with dependencies: {deps}")

            except Exception as e:
                print(f"❌ [COMPILE DEBUG] Failed to compile node {node_config.get('id', 'unknown')}: {e}")
                logger.error(f"Failed to compile step node {node_config.get('id', 'unknown')}: {e}")
                raise

        print(f"🔧 [COMPILE DEBUG] Step compilation completed: {len(self.step_nodes)} nodes, dependencies: {self.dependencies}")
        logger.debug(f"Compiled step {self.step_number} with {len(self.step_nodes)} nodes")

    def _compile_step_node(self, node_config: Dict[str, Any]) -> StepNode:
        """Compile a single step node configuration with V2 enhanced operator support."""
        node_id = node_config['id']

        # Compile selector
        selector_config = node_config.get('selector', {})
        selector_type = selector_config.get('type', 'all_agents')
        selector_func, selector_params = self._compile_selector(selector_config)

        # V2: Compile enhanced operator configurations
        operators_config = node_config.get('operators', [])
        if not operators_config:
            # Backward compatibility: check for single 'operator' field
            single_operator = node_config.get('operator')
            if single_operator:
                operators_config = [single_operator]

        operator_configs = []
        for i, op_config in enumerate(operators_config):
            # V2: Extract operator id (required for V2)
            op_id = op_config.get('id', f"op_{i}")  # Auto-generate if missing
            op_type = op_config.get('type', 'unknown')

            # Compile operator function
            op_func, base_params = self._compile_operator(op_config)

            # V2: Enhanced params support (will be rendered at runtime)
            params = op_config.copy()  # Start with full config
            params.update(base_params)  # Merge compiled parameters

            operator_config = OperatorConfig(
                id=op_id,
                type=op_type,
                func=op_func,
                params=params,  # V2: Enhanced params with potential JMESPath expressions
                base_params=base_params  # Original compiled params
            )
            operator_configs.append(operator_config)

        # Compile converter
        converter_config = node_config.get('converter', {})
        converter_type = converter_config.get('type') if converter_config else None
        converter_func, converter_params = self._compile_converter(converter_config)

        # Get inputs configuration
        inputs_config = node_config.get('inputs', {})

        # Get dependencies
        dependencies = node_config.get('dependencies', [])

        # V2: Get concurrency limit
        concurrency_limit = node_config.get('concurrency_limit', 200)

        return StepNode(
            id=node_id,
            selector_func=selector_func,
            selector_type=selector_type,
            operator_configs=operator_configs,  # V2: Enhanced operator configurations
            converter_func=converter_func,
            converter_type=converter_type,
            inputs_config=inputs_config,
            selector_params=selector_params,
            converter_params=converter_params,
            dependencies=dependencies,
            concurrency_limit=concurrency_limit  # V2: Configurable concurrency
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
        elif selector_type == 'by_archetype':
            return self._by_archetype_selector, params
        elif selector_type == 'by_id':
            return self._by_id_selector, params
        elif selector_type == 'environment':
            return self._environment_selector, params
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
        operator_type = operator_config.get('type')
        params = {k: v for k, v in operator_config.items() if k != 'type'}

        if operator_type == 'action':
            raise ValueError(
                "Operator type 'action' is no longer supported. "
                "请改为通过 instruct/interview 的 action_tags 让 LLM 主动调用动作能力。"
            )

        if operator_type == 'instruct':
            return self._instruct_operator, params

        elif operator_type == 'interview':
            return self._interview_operator, params

        elif operator_type == 'rule':
            rule_name = operator_config.get('name')
            if rule_name:
                return self._create_rule_operator(rule_name), params
            else:
                raise ValueError("Rule operator missing 'name'")

        elif operator_type == 'behavior':
            behavior_name = operator_config.get('name')
            if behavior_name:
                return self._create_behavior_operator(behavior_name), params
            else:
                raise ValueError("Behavior operator missing 'name'")

        elif operator_type == 'custom':
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
            print(f"🔧 [COMPILE DEBUG] No converter configured")
            return None, {}

        converter_type = converter_config.get('type')
        params = {k: v for k, v in converter_config.items() if k != 'type'}

        print(f"🔧 [COMPILE DEBUG] Compiling converter: type={converter_type}")
        print(f"🔧 [COMPILE DEBUG] Converter params keys: {list(params.keys())}")

        if converter_type == 'jmespath':
            expression = params.get('expression', '@')
            print(f"🔧 [COMPILE DEBUG] JMESPath converter with expression: {expression}")
            return self._jmespath_converter, params
        elif converter_type == 'summary':
            print(f"🔧 [COMPILE DEBUG] Summary converter")
            return self._summary_converter, params
        elif converter_type == 'passthrough':
            print(f"🔧 [COMPILE DEBUG] Passthrough converter")
            return self._passthrough_converter, params
        elif converter_type == 'custom':
            func_name = converter_config.get('function')
            print(f"🔧 [COMPILE DEBUG] Custom converter: {func_name}")
            if func_name and func_name in self.registry.converters:
                converter_func = self.registry.converters[func_name]['function']
                print(f"🔧 [COMPILE DEBUG] Found custom converter function: {func_name}")
                return converter_func, params
            else:
                print(f"❌ [COMPILE DEBUG] Custom converter function '{func_name}' not found")
                raise ValueError(f"Custom converter function '{func_name}' not found")
        else:
            print(f"❌ [COMPILE DEBUG] Unknown converter type: {converter_type}")
            raise ValueError(f"Unknown converter type: {converter_type}")

    async def execute(self, world: World, parent_context: ExecutionContext) -> Dict[str, Any]:
        """Execute this step's DAG, directly modify world_state, return execution metrics.

        Args:
            world_state: Current world state to modify directly
            parent_context: Execution context from parent Schedule

        Returns:
            Execution metrics and results for this step
        """
        # 同步 step 编号，确保事件与差异文件按真实步骤编号归档
        self.step_number = world.step

        print(f"🚀 [STEP DEBUG] Starting StepFlow execution for step {self.step_number}")
        print(f"🚀 [STEP DEBUG] Number of nodes to execute: {len(self.step_nodes)}")
        print(f"🚀 [STEP DEBUG] Node IDs: {[node.id for node in self.step_nodes]}")
        logger.debug(f"Executing step {self.step_number} with {len(self.step_nodes)} nodes")
        self._log_schedule(
            "DEBUG",
            ScheduleEvent.STEP_FLOW_STARTED.value,
            total_nodes=len(self.step_nodes),
        )

        # Reset step context
        self.step_context = {}
        self._context_builder.reset(self.step_number)
        start_time = time.time()

        try:
            # Use DAG execution with proper dependency resolution
            print(f"📊 [STEP DEBUG] Attempting DAG execution...")
            execution_results = await self._execute_dag_nodes(world, parent_context)
            print(f"✅ [STEP DEBUG] DAG execution completed successfully")

        except ImportError:
            logger.warning("DAG engine not available, falling back to sequential execution")
            print(f"⚠️ [STEP DEBUG] DAG execution failed, falling back to sequential execution")
            # Fallback to sequential execution
            execution_results = await self._execute_sequential(world, parent_context)

        execution_time = time.time() - start_time
        print(f"⏱️ [STEP DEBUG] Step {self.step_number} execution completed in {execution_time:.3f}s")
        print(f"📊 [STEP DEBUG] Final step_context keys: {list(self.step_context.keys())}")

        jmespath_root = self._context_builder.build_root(world=world, step_context=self.step_context)

        self._log_schedule(
            "INFO",
            ScheduleEvent.STEP_FLOW_COMPLETED.value,
            total_nodes=len(self.step_nodes),
            duration_sec=execution_time,
        )

        return {
            "step_number": self.step_number,
            "nodes_executed": len(self.step_nodes),
            "execution_time": execution_time,
            "step_context": self.step_context.copy(),
            "results": execution_results,
            "jmespath_root": jmespath_root,
        }

    async def _execute_dag_nodes(self, world: World, parent_context: ExecutionContext) -> List[Dict[str, Any]]:
        """Execute nodes using DAG dependency resolution."""
        print(f"📊 [DAG DEBUG] Starting DAG node execution...")
        print(f"📊 [DAG DEBUG] Dependencies: {self.dependencies}")

        # For now, implement basic topological sort
        # In full implementation, this would use a proper DAG execution engine

        executed = set()
        results = []

        # Simple dependency resolution - execute nodes when all dependencies are met
        iteration = 0
        while len(executed) < len(self.step_nodes):
            iteration += 1
            print(f"📊 [DAG DEBUG] Iteration {iteration}: executed={executed}, remaining={len(self.step_nodes) - len(executed)}")
            progress_made = False

            for node in self.step_nodes:
                if node.id in executed:
                    continue

                # Check if all dependencies are satisfied
                deps_satisfied = all(dep in executed for dep in node.dependencies)
                print(f"📊 [DAG DEBUG] Node {node.id}: dependencies={node.dependencies}, satisfied={deps_satisfied}")

                if deps_satisfied:
                    print(f"🎯 [DAG DEBUG] Executing node {node.id}...")
                    result = await self._execute_node(node, world, parent_context)
                    results.append(result)
                    executed.add(node.id)
                    progress_made = True
                    print(f"✅ [DAG DEBUG] Node {node.id} completed successfully")

            if not progress_made:
                print(f"💥 [DAG DEBUG] No progress made - circular dependency detected!")
                print(f"💥 [DAG DEBUG] Executed: {executed}")
                print(f"💥 [DAG DEBUG] Remaining nodes: {[n.id for n in self.step_nodes if n.id not in executed]}")
                print(f"💥 [DAG DEBUG] Dependencies: {self.dependencies}")
                raise RuntimeError("Circular dependency detected in step nodes")

        print(f"🎉 [DAG DEBUG] All nodes executed successfully!")
        return results

    async def _execute_sequential(self, world: World, parent_context: ExecutionContext) -> List[Dict[str, Any]]:
        """Execute nodes sequentially without dependency resolution."""
        results = []

        for node in self.step_nodes:
            result = await self._execute_node(node, world_state, parent_context)
            results.append(result)

        return results

    async def _execute_node(self, node: StepNode, world: World, parent_context: ExecutionContext) -> Dict[str, Any]:
        """Execute a single step node with V2 parallel Agent pipeline support."""
        print(f"🚀 [DEBUG] Starting node execution: {node.id}")
        logger.debug(f"Executing node: {node.id}")
        node_start_time = time.time()
        operator_types = [cfg.type for cfg in node.operator_configs]
        operator_ids = [cfg.id for cfg in node.operator_configs]
        self._log_schedule(
            "DEBUG",
            ScheduleEvent.NODE_STARTED.value,
            **{
                LogField.NODE_ID.value: node.id,
                LogField.SELECTOR_TYPE.value: node.selector_type,
                LogField.OPERATOR_TYPES.value: operator_types,
                LogField.OPERATOR_IDS.value: operator_ids,
                LogField.CONVERTER_TYPE.value: node.converter_type,
                LogField.DEPENDENCIES.value: node.dependencies,
                LogField.CONCURRENCY_LIMIT.value: node.concurrency_limit,
            },
        )

        # Create execution context for this node
        node_context = ExecutionContext(
            world=world,
            step=self,
            node=node,
            caller=parent_context.caller,
            event_logger=parent_context.event_logger,
            log_context=parent_context.log_context,
        )

        # Resolve inputs from step context
        node.inputs = self._resolve_inputs(node.inputs_config)
        print(f"📋 [DEBUG] Node {node.id} inputs resolved: {list(node.inputs.keys()) if node.inputs else 'None'}")
        self._context_builder.record_node_inputs(node.id, node.inputs)

        # Create context stack for this node execution
        from .context_stack import ContextStack
        current_stack = world.get_context_stack()
        node_stack = current_stack.push_node(node.id, node_type="schedule_node")
        world.set_context_stack(node_stack)

        node_result: Dict[str, Any] = {}
        diff_payload: Optional[NodeDiffPayload] = None

        # Execute node within a transaction
        try:
            with world.transaction_manager.transaction(f"step_{self.step_number}", node.id, node_stack) as tx:
                # Phase 1: Selector - Select targets
                print(f"🎯 [DEBUG] Node {node.id} - Phase 1: Running selector...")
                selector_param_keys = sorted(node.selector_params.keys()) if node.selector_params else []
                self._log_schedule(
                    "DEBUG",
                    ScheduleEvent.NODE_SELECTOR_STARTED.value,
                    **{
                        LogField.NODE_ID.value: node.id,
                        LogField.SELECTOR_TYPE.value: node.selector_type,
                        LogField.SELECTOR_PARAM_KEYS.value: selector_param_keys,
                    },
                )

                selector_start = time.time()
                try:
                    targets = await node.selector_func(node.selector_params, node_context)
                except Exception as exc:
                    self._log_schedule(
                        "ERROR",
                        ScheduleEvent.NODE_SELECTOR_FAILED.value,
                        **{
                            LogField.NODE_ID.value: node.id,
                            LogField.SELECTOR_TYPE.value: node.selector_type,
                            LogField.ERROR.value: str(exc),
                            LogField.TRACEBACK.value: traceback.format_exc(),
                        },
                    )
                    raise

                selector_duration = time.time() - selector_start
                targets_count = len(targets) if targets else 0
                print(f"🎯 [DEBUG] Node {node.id} - Selector found {targets_count} targets")
                self._context_builder.record_selector(node.id, node.selector_params, targets)
                self._log_schedule(
                    "INFO",
                    ScheduleEvent.NODE_SELECTOR_COMPLETED.value,
                    **{
                        LogField.NODE_ID.value: node.id,
                        LogField.SELECTOR_TYPE.value: node.selector_type,
                        LogField.TARGETS_COUNT.value: targets_count,
                        LogField.TARGET_IDS_SAMPLE.value: self._sample_target_ids(targets),
                        LogField.SELECTOR_DURATION_SEC.value: selector_duration,
                    },
                )

                # V2: Phase 2: Execution Mode Detection and Dispatch
                if not targets:
                    execution_mode = "empty"
                    print(f"⚠️ [DEBUG] Node {node.id} - No targets selected, skipping operators")
                    operator_results = []
                elif self._is_environment_mode(targets):
                    execution_mode = "global"
                    print(f"🌍 [DEBUG] Node {node.id} - Phase 2: Using GLOBAL mode")
                    operator_results = await self._execute_global_mode(node, targets, world, node_context, node_stack)
                else:
                    execution_mode = "parallel_agent"
                    print(f"👥 [DEBUG] Node {node.id} - Phase 2: Using PARALLEL AGENT mode")
                    operator_results = await self._execute_parallel_agent_mode(node, targets, world, node_context, node_stack)

                self._log_schedule(
                    "DEBUG",
                    ScheduleEvent.NODE_EXECUTION_MODE_SELECTED.value,
                    **{
                        LogField.NODE_ID.value: node.id,
                        LogField.EXECUTION_MODE.value: execution_mode,
                        LogField.TARGETS_COUNT.value: targets_count,
                    },
                )

                if execution_mode == "empty":
                    for operator_config in node.operator_configs:
                        self._log_schedule(
                            "INFO",
                            ScheduleEvent.OPERATOR_SKIPPED.value,
                            **{
                                LogField.NODE_ID.value: node.id,
                                LogField.OPERATOR_ID.value: operator_config.id,
                                LogField.EXECUTION_MODE.value: execution_mode,
                                LogField.TARGETS_COUNT.value: 0,
                            },
                        )

                print(f"⚡ [DEBUG] Node {node.id} - Phase 2 completed with {len(operator_results) if operator_results else 0} operator results")

                # Phase 3: Converter - Aggregate and transform results
                print(f"🔄 [DEBUG] Node {node.id} - Phase 3: Running converter...")

                # 🔧 CRITICAL DEBUG: Verify agent state before converter execution
                print(f"🔍 [PRE-CONVERTER DEBUG] Sampling agent properties state before converter:")
                for agent_id, agent_data in list(world.agents_data.items())[:3]:  # Sample first 3 agents
                    properties = agent_data.get("properties", {})
                    print(f"🔍 [PRE-CONVERTER DEBUG] Agent {agent_id}: properties keys={list(properties.keys())}")

                converted_output = {}
                if node.converter_func:
                    converter_param_keys = sorted(node.converter_params.keys()) if node.converter_params else []
                    self._log_schedule(
                        "DEBUG",
                        ScheduleEvent.CONVERTER_STARTED.value,
                        **{
                            LogField.NODE_ID.value: node.id,
                            LogField.CONVERTER_TYPE.value: node.converter_type,
                            LogField.CONVERTER_PARAMS.value: converter_param_keys,
                        },
                    )
                    converter_start = time.time()
                    try:
                        print(f"🔄 [DEBUG] Node {node.id} - Calling converter with world (God View mode enabled)")
                        print(f"🔄 [DEBUG] Node {node.id} - Available world data: agents={len(world.agents_data)}, step={world.step}")
                        converted_output = await node.converter_func(operator_results, node.converter_params, node_context, world)
                        converter_duration = time.time() - converter_start
                        print(f"✅ [DEBUG] Node {node.id} - Converter completed successfully")
                        print(f"✅ [DEBUG] Node {node.id} - Converter output keys: {list(converted_output.keys()) if converted_output else 'None'}")
                        self._log_schedule(
                            "INFO",
                            ScheduleEvent.CONVERTER_COMPLETED.value,
                            **{
                                LogField.NODE_ID.value: node.id,
                                LogField.CONVERTER_TYPE.value: node.converter_type,
                                LogField.DURATION_SEC.value: converter_duration,
                                LogField.OUTPUT_KEYS.value: list(converted_output.keys()) if isinstance(converted_output, dict) else [],
                            },
                        )
                    except Exception as e:
                        converter_duration = time.time() - converter_start
                        print(f"❌ [DEBUG] Node {node.id} - Converter error: {e}")
                        logger.error(f"Error in converter of node {node.id}: {e}")
                        self._log_schedule(
                            "ERROR",
                            ScheduleEvent.CONVERTER_FAILED.value,
                            **{
                                LogField.NODE_ID.value: node.id,
                                LogField.CONVERTER_TYPE.value: node.converter_type,
                                LogField.ERROR.value: str(e),
                                LogField.TRACEBACK.value: traceback.format_exc(),
                                LogField.DURATION_SEC.value: converter_duration,
                            },
                        )
                        converted_output = {"converter_error": str(e)}
                else:
                    print(f"⏭️ [DEBUG] Node {node.id} - No converter configured, skipping")
                    self._log_schedule(
                        "DEBUG",
                        ScheduleEvent.CONVERTER_SKIPPED.value,
                        **{
                            LogField.NODE_ID.value: node.id,
                            LogField.CONVERTER_TYPE.value: node.converter_type,
                        },
                    )

                # Update step context with outputs
                if converted_output:
                    self.step_context[node.id] = converted_output
                    self._context_builder.record_converter_output(node.id, converted_output)
                    print(f"💾 [DEBUG] Node {node.id} - Updated step_context with converter output")

                print(f"🎉 [DEBUG] Node {node.id} completed successfully")
                logger.debug(f"Node {node.id} completed with {len(operator_results) if operator_results else 0} results")

                node_result = {
                    "node_id": node.id,
                    "targets_count": len(targets) if targets else 0,
                    "operators_executed": len(operator_results) if operator_results else 0,
                    "operator_results": operator_results,
                    "converted_output": converted_output,
                    "success_count": len([r for r in operator_results if hasattr(r, 'status') and r.status == "success"]) if operator_results else 0,
                    "error_count": len([r for r in operator_results if hasattr(r, 'status') and r.status == "error"]) if operator_results else 0
                }

                diff_changes = self._build_node_diff(
                    world,
                    tx.pending_events,
                )
                if diff_changes:
                    diff_payload = NodeDiffPayload(
                        step_id=self.step_number,
                        node_id=node.id,
                        changes=diff_changes,
                        context_stack=tx.context_stack.to_list(),
                    )
                duration_sec = time.time() - node_start_time
                self._log_schedule(
                    "INFO",
                    ScheduleEvent.NODE_COMPLETED.value,
                    **{
                        LogField.NODE_ID.value: node.id,
                        LogField.SELECTOR_TYPE.value: node.selector_type,
                        LogField.OPERATOR_TYPES.value: [cfg.type for cfg in node.operator_configs],
                        LogField.CONVERTER_TYPE.value: node.converter_type,
                        LogField.DURATION_SEC.value: duration_sec,
                        LogField.TARGETS_COUNT.value: node_result.get("targets_count", 0),
                        LogField.OPERATORS_EXECUTED.value: node_result.get("operators_executed", 0),
                        LogField.SUCCESS_COUNT.value: node_result.get("success_count", 0),
                        LogField.ERROR_COUNT.value: node_result.get("error_count", 0),
                    },
                )

        except Exception as e:
            print(f"💥 [DEBUG] Node {node.id} execution failed: {e}")
            logger.error(f"Failed to execute node {node.id}: {e}")
            self._log_schedule(
                "ERROR",
                ScheduleEvent.NODE_FAILED.value,
                **{
                    LogField.NODE_ID.value: node.id,
                    LogField.SELECTOR_TYPE.value: node.selector_type,
                    LogField.OPERATOR_TYPES.value: [cfg.type for cfg in node.operator_configs],
                    LogField.CONVERTER_TYPE.value: node.converter_type,
                    LogField.DEPENDENCIES.value: node.dependencies,
                    LogField.ERROR.value: str(e),
                    LogField.TRACEBACK.value: traceback.format_exc(),
                    LogField.DURATION_SEC.value: time.time() - node_start_time,
                },
            )
            raise
        finally:
            # Restore previous context stack
            world.set_context_stack(current_stack)

        dispatcher = world.get_node_diff_dispatcher()
        if diff_payload and dispatcher:
            dispatcher.dispatch(diff_payload)

        return node_result

    def _build_node_diff(
        self,
        world: World,
        pending_events: List[BaseEvent],
    ) -> List[Dict[str, Any]]:
        """根据节点事务期间生成的事件构建紧凑 diff。"""
        changes: List[Dict[str, Any]] = []
        seen_keys: Set[Tuple[str, Optional[str], Tuple[str, ...]]] = set()
        current_agents = world.agents_data
        current_environment = world.environment_data

        for event in pending_events:
            if not isinstance(event, StateChangeEvent):
                continue

            path = list(event.path or [])
            key = (event.target_type, event.target_id, tuple(path))
            if key in seen_keys:
                continue
            seen_keys.add(key)

            if event.target_type == "agent":
                container = current_agents.get(event.target_id or "", {})
                new_value = self._resolve_nested_value(container, path)
                target = {"type": "agent", "id": event.target_id}
            else:
                container = self._resolve_environment_container(current_environment, event.target_id)
                new_value = self._resolve_nested_value(container, path)
                target = {"type": "environment"}

            old_value = copy.deepcopy(getattr(event, "old_value", None))

            if old_value == new_value:
                continue

            change_record: Dict[str, Any] = {
                "target": target,
                "path": path,
                "operation": event.operation,
                "old_value": old_value,
                "new_value": copy.deepcopy(new_value),
            }
            if getattr(event, "event_id", None):
                change_record["event_id"] = event.event_id
            if target.get("id") is None:
                target.pop("id", None)

            changes.append(change_record)

        return changes

    @staticmethod
    def _sample_target_ids(targets: Optional[List[Any]], limit: int = 5) -> List[str]:
        """提取目标对象的标识符，用于日志样本展示。"""
        if not targets:
            return []

        samples: List[str] = []
        for target in targets[:limit]:
            identifier = getattr(target, "id", None) or getattr(target, "name", None)
            if identifier is None and isinstance(target, dict):
                identifier = target.get("id") or target.get("name")
            if identifier is not None:
                samples.append(str(identifier))
        return samples

    @staticmethod
    def _resolve_environment_container(environment_data: Any, target_id: Optional[str]) -> Any:
        """根据事件目标选择正确的环境容器作为 diff 比较基准。"""
        if not isinstance(environment_data, dict):
            return environment_data

        if target_id:
            container = environment_data.get(target_id)
            if container is not None:
                return container

        return environment_data

    @staticmethod
    def _resolve_nested_value(container: Any, path: List[str]) -> Any:
        """按路径解析嵌套值，兼容字典与列表。"""
        current = container
        for segment in path:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(segment)
            elif isinstance(current, list):
                try:
                    index = int(segment)
                except (TypeError, ValueError):
                    return None
                if index < 0 or index >= len(current):
                    return None
                current = current[index]
            else:
                return None
        return current

    def _is_environment_mode(self, targets) -> bool:
        """Determine if execution should be in global/environment mode."""
        if not targets:
            return False

        # Import here to avoid circular imports
        from .environment import Environment

        # Check if the first target is an Environment instance
        # In global mode, typically only one Environment is selected
        return isinstance(targets[0], Environment) or hasattr(targets[0], 'snapshot')

    async def _execute_global_mode(self, node: StepNode, targets, world: World, node_context, node_stack) -> List:
        """Execute operators in global mode (V1 compatibility)."""
        logger.debug(f"Executing node {node.id} in global mode")

        operator_results = []

        # V1 compatibility: Execute operators sequentially on all targets
        for i, operator_config in enumerate(node.operator_configs):
            try:
                operator_param_keys = sorted(operator_config.params.keys()) if operator_config.params else []
                operator_start = time.time()
                self._log_schedule(
                    "DEBUG",
                    ScheduleEvent.OPERATOR_STARTED.value,
                    **{
                        LogField.NODE_ID.value: node.id,
                        LogField.OPERATOR_ID.value: operator_config.id,
                        LogField.OPERATOR_TYPES.value: [operator_config.type],
                        LogField.OPERATOR_PARAM_KEYS.value: operator_param_keys,
                        LogField.EXECUTION_MODE.value: "global",
                        LogField.TARGETS_COUNT.value: len(targets) if targets else 0,
                    },
                )

                # Update context stack for operator execution
                op_stack = node_stack.push_operator(f"{operator_config.func.__name__}_{i}")
                world.set_context_stack(op_stack)

                # Render parameters with unified context builder
                merged_context = world.get_context_stack().to_dict()
                agent_identifier = None
                if targets:
                    first_target = targets[0]
                    agent_identifier = getattr(first_target, "id", None) or getattr(first_target, "name", None)
                if agent_identifier is None:
                    agent_identifier = "environment" if self._is_environment_mode(targets) else "global"

                rendered_params = self._render_params_with_context(
                    operator_config.params,
                    merged_context,
                    world,
                    node.inputs,
                    node,
                    agent_id=str(agent_identifier),
                )

                # Execute operator on all targets
                result = await operator_config.func(targets, rendered_params, node_context)

                # Ensure result is BaseOperatorResult
                if not isinstance(result, BaseOperatorResult):
                    logger.warning(f"Operator {operator_config.func.__name__} returned non-BaseOperatorResult: {type(result)}")
                    # Convert to BaseOperatorResult for compatibility
                    result = BaseOperatorResult(
                        agent_id="global",
                        status="success",
                        value=result,
                        metadata={"converted_from_legacy": True, "operator_id": operator_config.id}
                    )

                if result.metadata is None:
                    result.metadata = {}
                result.metadata.setdefault("operator_id", operator_config.id)
                result.metadata.setdefault("operator_type", operator_config.type)
                result.metadata.setdefault("node_id", node.id)
                if agent_identifier:
                    result.metadata.setdefault("agent_id", agent_identifier)

                self._record_operator_result(
                    node=node,
                    operator_config=operator_config,
                    agent_identifier=str(agent_identifier),
                    result=result,
                    target_store=None,
                    rendered_params=rendered_params,  # 传入渲染后的参数
                )

                operator_results.append(result)

                duration = time.time() - operator_start
                success_count = 1 if getattr(result, "status", None) == "success" else 0
                error_count = 0 if success_count else 1
                self._log_schedule(
                    "INFO",
                    ScheduleEvent.OPERATOR_COMPLETED.value,
                    **{
                        LogField.NODE_ID.value: node.id,
                        LogField.OPERATOR_ID.value: operator_config.id,
                        LogField.OPERATOR_TYPES.value: [operator_config.type],
                        LogField.DURATION_SEC.value: duration,
                        LogField.RESULT_COUNT.value: 1,
                        LogField.SUCCESS_COUNT.value: success_count,
                        LogField.ERROR_COUNT.value: error_count,
                        LogField.RENDERED_PARAM_KEYS.value: sorted(rendered_params.keys()) if isinstance(rendered_params, dict) else [],
                    },
                )

            except Exception as e:
                duration = time.time() - operator_start
                logger.error(f"Error in operator {i} of node {node.id}: {e}")
                self._log_schedule(
                    "ERROR",
                    ScheduleEvent.OPERATOR_FAILED.value,
                    **{
                        LogField.NODE_ID.value: node.id,
                        LogField.OPERATOR_ID.value: operator_config.id,
                        LogField.OPERATOR_TYPES.value: [operator_config.type],
                        LogField.ERROR.value: str(e),
                        LogField.TRACEBACK.value: traceback.format_exc(),
                        LogField.DURATION_SEC.value: duration,
                    },
                )
                # Create error result
                error_result = BaseOperatorResult(
                    agent_id="global",
                    status="error",
                    value=None,
                    error_message=str(e),
                    metadata={
                        "operator_index": i,
                        "operator_id": operator_config.id,
                        "operator_type": operator_config.type,
                        "node_id": node.id,
                        "agent_id": str(agent_identifier) if agent_identifier else "global",
                    }
                )
                self._record_operator_result(
                    node=node,
                    operator_config=operator_config,
                    agent_identifier=str(agent_identifier),
                    result=error_result,
                    target_store=None,
                    rendered_params=locals().get("rendered_params"),
                )
                operator_results.append(error_result)

                # Restore node context stack after operator error
                world.set_context_stack(node_stack)

        return operator_results

    async def _execute_parallel_agent_mode(self, node: StepNode, agents, world: World, node_context, node_stack) -> List:
        """V2: Execute operators in parallel Agent mode - the core innovation."""
        logger.debug(f"Executing node {node.id} in parallel Agent mode for {len(agents)} agents")

        import asyncio
        from .agent.core import Agent

        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(node.concurrency_limit)

        # V2: Execute operators sequentially, but agents in parallel for each operator
        all_agent_results = []

        for operator_config in node.operator_configs:
            logger.debug(f"Executing operator {operator_config.id} for {len(agents)} agents")

            # Update context stack for this operator
            op_stack = node_stack.push_operator(operator_config.id)
            world.set_context_stack(op_stack)

            operator_param_keys = sorted(operator_config.params.keys()) if operator_config.params else []
            operator_start = time.time()
            self._log_schedule(
                "DEBUG",
                ScheduleEvent.OPERATOR_STARTED.value,
                **{
                    LogField.NODE_ID.value: node.id,
                    LogField.OPERATOR_ID.value: operator_config.id,
                    LogField.OPERATOR_TYPES.value: [operator_config.type],
                    LogField.OPERATOR_PARAM_KEYS.value: operator_param_keys,
                    LogField.EXECUTION_MODE.value: "parallel_agent",
                    LogField.TARGETS_COUNT.value: len(agents),
                    LogField.CONCURRENCY_LIMIT.value: node.concurrency_limit,
                },
            )

            async def execute_agent_operator(agent: Agent):
                """Execute single operator for single agent with full V2 context support."""
                async with semaphore:
                    try:
                        # V2: Create/update agent local context (代数效应栈)
                        if not hasattr(agent, '_local_context'):
                            agent._local_context = {}

                        agent_local_context = agent._local_context
                        local_context_version = agent_local_context.get("__version__", 0)

                        # V2: Merge contexts (代数效应) - global context + agent local context
                        global_context_dict = world.get_context_stack().to_dict()

                        # V2: Convert BaseOperatorResult objects to字典形式，确保 structured_output 暴露
                        serialized_local_context: Dict[str, Any] = {}
                        for op_id, context_value in list(agent_local_context.items()):
                            if op_id == "__version__":
                                continue
                            if isinstance(context_value, dict):
                                serialized_local_context[op_id] = context_value
                                continue
                            if isinstance(context_value, BaseOperatorResult):
                                inferred_agent = (
                                    (context_value.metadata or {}).get("agent_id")
                                    or getattr(context_value, "agent_id", None)
                                    or agent.id
                                )
                                serialized = self._serialize_operator_result(
                                    context_value,
                                    agent_identifier=inferred_agent,
                                )
                                agent_local_context[op_id] = serialized
                                serialized_local_context[op_id] = serialized
                                continue
                            serialized_local_context[op_id] = context_value

                        merged_context = {
                            **global_context_dict,  # 全局 ContextStack 内容 (较低优先级)
                            **serialized_local_context,  # Agent 本地上下文（较高优先级）
                        }

                        world_version = world.get_state_version() if hasattr(world, "get_state_version") else 0
                        cache_key = (node.id, operator_config.id, agent.id, world_version, local_context_version)
                        cached_params = self._render_cache.get(cache_key)
                        if cached_params is not None:
                            rendered_params = copy.deepcopy(cached_params)
                        else:
                            rendered_params = self._render_params_with_context(
                                operator_config.params,
                                merged_context,
                                world,
                                node.inputs,
                                node,
                                agent_id=agent.id,
                            )
                            self._render_cache[cache_key] = copy.deepcopy(rendered_params)

                        # 🔍 单点进度日志：标记当前 agent/operator 开始执行
                        print(
                            "🔍 [PARAMS DEBUG] agent=%s operator=%s rendered_keys=%s"
                            % (agent.id, operator_config.id, list(rendered_params.keys()))
                        )

                        # Execute operator on single agent（为当前 operator 注入 operator_id）
                        op_context = replace(node_context, operator_id=operator_config.id)
                        result = await operator_config.func([agent], rendered_params, op_context)

                        # Ensure result is BaseOperatorResult
                        if not isinstance(result, BaseOperatorResult):
                            result = BaseOperatorResult(
                                agent_id=agent.id,
                                status="success",
                                value=result,
                                metadata={"operator_id": operator_config.id, "converted": True}
                            )

                        if result.metadata is None:
                            result.metadata = {}
                        result.metadata.setdefault("operator_id", operator_config.id)
                        result.metadata.setdefault("operator_type", operator_config.type)
                        result.metadata.setdefault("node_id", node.id)
                        result.metadata.setdefault("agent_id", agent.id)

                        self._record_operator_result(
                            node=node,
                            operator_config=operator_config,
                            agent_identifier=agent.id,
                            result=result,
                            target_store=agent_local_context,
                            rendered_params=rendered_params,
                        )

                        logger.debug(f"Agent {agent.id} completed operator {operator_config.id}: {result.status}")
                        return result

                    except Exception as e:
                        logger.error(f"Agent {agent.id} failed operator {operator_config.id}: {e}")
                        # V2: Create error result and continue (resilient execution)
                        error_result = BaseOperatorResult(
                            agent_id=agent.id,
                            status="error",
                            value=None,
                            error_message=str(e),
                            metadata={
                                "operator_id": operator_config.id,
                                "operator_type": operator_config.type,
                                "node_id": node.id,
                                "agent_id": agent.id,
                            }
                        )

                        # V2: Still push error result to agent context for debugging
                        if not hasattr(agent, '_local_context'):
                            agent._local_context = {}
                        self._record_operator_result(
                            node=node,
                            operator_config=operator_config,
                            agent_identifier=agent.id,
                            result=error_result,
                            target_store=agent_local_context,
                            rendered_params=locals().get("rendered_params"),
                        )
                        return error_result

            # V2: Execute all agents concurrently for this operator
            operator_results = await asyncio.gather(*[
                execute_agent_operator(agent) for agent in agents
            ], return_exceptions=True)

            # Handle exceptions from gather
            processed_results = []
            aggregated_errors: List[tuple[str, str]] = []
            for i, result in enumerate(operator_results):
                if isinstance(result, Exception):
                    logger.error(f"Exception in agent {agents[i].id} operator {operator_config.id}: {result}")
                    error_result = BaseOperatorResult(
                        agent_id=agents[i].id,
                        status="error",
                        value=None,
                        error_message=str(result),
                        metadata={
                            "operator_id": operator_config.id,
                            "operator_type": operator_config.type,
                            "node_id": node.id,
                            "exception": True,
                            "agent_id": agents[i].id,
                        }
                    )
                    target_store = getattr(agents[i], '_local_context', None)
                    store_dict = target_store if isinstance(target_store, dict) else None
                    self._record_operator_result(
                        node=node,
                        operator_config=operator_config,
                        agent_identifier=agents[i].id,
                        result=error_result,
                        target_store=store_dict,
                        rendered_params=None,
                    )
                    processed_results.append(error_result)
                    aggregated_errors.append((agents[i].id, str(result)))
                else:
                    processed_results.append(result)

            all_agent_results.extend(processed_results)

            # Restore node context stack after operator completion
            world.set_context_stack(node_stack)

            duration = time.time() - operator_start
            result_count = len(processed_results)
            success_count = sum(1 for r in processed_results if getattr(r, "status", None) == "success")
            error_count = sum(1 for r in processed_results if getattr(r, "status", None) == "error")
            self._log_schedule(
                "INFO",
                ScheduleEvent.OPERATOR_COMPLETED.value,
                **{
                    LogField.NODE_ID.value: node.id,
                    LogField.OPERATOR_ID.value: operator_config.id,
                    LogField.OPERATOR_TYPES.value: [operator_config.type],
                    LogField.DURATION_SEC.value: duration,
                    LogField.RESULT_COUNT.value: result_count,
                    LogField.SUCCESS_COUNT.value: success_count,
                    LogField.ERROR_COUNT.value: error_count,
                    LogField.RENDERED_PARAM_KEYS.value: operator_param_keys,
                },
            )

            if aggregated_errors:
                failed_sample = [entry[0] for entry in aggregated_errors[:5]]
                error_message = f"agent_failures={len(aggregated_errors)}"
                self._log_schedule(
                    "WARNING",
                    ScheduleEvent.OPERATOR_FAILED.value,
                    **{
                        LogField.NODE_ID.value: node.id,
                        LogField.OPERATOR_ID.value: operator_config.id,
                        LogField.OPERATOR_TYPES.value: [operator_config.type],
                        LogField.ERROR.value: error_message,
                        LogField.DURATION_SEC.value: duration,
                        LogField.AGENT_BATCH.value: failed_sample,
                    },
                )

        logger.debug(f"Parallel Agent mode completed: {len(all_agent_results)} total results")
        return all_agent_results

    def _render_params_with_context(
        self,
        params: Dict[str, Any],
        merged_context: Dict[str, Any],
        world: World,
        inputs: Dict[str, Any],
        node: Optional[StepNode],
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """结合 Builder 上下文渲染 operator 参数，并处理 input_mapping。"""
        try:
            # Start with base params
            rendered = params.copy()

            # 构建统一的 JMESPath 根上下文
            root_context = self._context_builder.build_root(world=world, step_context=self.step_context)
            jmespath_data: Dict[str, Any] = dict(root_context)
            jmespath_data["inputs"] = dict(inputs or {})
            jmespath_data["world_step"] = world.step
            jmespath_data["world_agents_count"] = len(world.agents_data)
            jmespath_data["node_context"] = {
                "node_id": node.id if node else None,
                "step_number": self.step_number,
                "step_context": self.step_context,
            }
            if agent_id:
                jmespath_data["agent"] = {"id": agent_id}

            # 向下兼容：保留历史上下文字段
            jmespath_data["local_context"] = merged_context
            # 新增：提供语义更清晰的 local 命名空间（并保留 local_context 以向后兼容）
            jmespath_data["local"] = merged_context
            if "operators" in jmespath_data:
                jmespath_data["operators_map"] = jmespath_data["operators"]
            for key, value in merged_context.items():
                if key not in jmespath_data:
                    jmespath_data[key] = value

            # Handle input_mapping FIRST (unified with direct params context)
            # 🔧 核心修改：input_mapping 现在使用完整的 jmespath_data，与直接参数一致
            if "input_mapping" in rendered:
                input_mapping = rendered.pop("input_mapping")  # Remove from params
                print(f"🔄 [INPUT_MAPPING DEBUG] Processing input_mapping: {input_mapping}")
                print(f"🔄 [INPUT_MAPPING DEBUG] Available jmespath_data keys: {list(jmespath_data.keys())}")

                # Process each mapping entry
                for target_param, source_expression in input_mapping.items():
                    print(f"🔄 [INPUT_MAPPING DEBUG] Mapping {target_param} <- {source_expression}")

                    try:
                        # 🎯 关键改进：统一使用 jmespath_data（完整上下文）
                        # 支持两种语法：
                        # 1. 完整 JMESPath 表达式（带 j: 前缀）：j:step_context.node_id.field
                        # 2. 非表达式一律视为字面量（去除简写路径的隐式解析，提升严谨性）

                        is_expr, mapped_value = self._evaluate_jmes_expression(source_expression, jmespath_data)

                        if is_expr:
                            # 成功解析为 JMESPath 表达式
                            rendered[target_param] = mapped_value
                            print(f"✅ [INPUT_MAPPING DEBUG] Mapped {target_param} = {mapped_value} (via JMESPath)")
                        else:
                            # 不是 JMESPath 表达式（没有 j: 前缀），当作字面量
                            rendered[target_param] = source_expression
                            print(f"⚠️ [INPUT_MAPPING DEBUG] Mapped {target_param} = {source_expression} (literal)")

                    except Exception as e:
                        print(f"❌ [INPUT_MAPPING DEBUG] Failed to map {target_param}: {e}")
                        print(f"❌ [INPUT_MAPPING DEBUG] Available jmespath_data keys: {list(jmespath_data.keys())}")
                        print(f"❌ [INPUT_MAPPING DEBUG] Available operators: {list(jmespath_data.get('operators', {}).keys())}")
                        print(f"❌ [INPUT_MAPPING DEBUG] Available step_context nodes: {list(jmespath_data.get('step_context', {}).keys())}")
                        # 出错时不再进行简写路径回退，直接作为字面量
                        rendered[target_param] = source_expression

            # Render JMESPath expressions in params
            rendered = self._render_jmespath_recursive(rendered, jmespath_data)

            return rendered

        except Exception as e:
            warning_msg = f"[PARAM_RENDER WARNING] Error rendering params with context: {e}"
            logger.warning(warning_msg)
            print(warning_msg)
            logger.debug(f"Context keys: {list(merged_context.keys())}")
            # Return original params as fallback
            return params

    def _render_jmespath_recursive(self, obj, context_data):
        """Recursively render JMESPath expressions in nested data structures."""

        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                result[key] = self._render_jmespath_recursive(value, context_data)
            return result
        elif isinstance(obj, list):
            return [self._render_jmespath_recursive(item, context_data) for item in obj]
        elif isinstance(obj, str):
            is_expr, value = self._evaluate_jmes_expression(obj, context_data)
            return value if is_expr else obj
        else:
            return obj

    def _extract_jmes_expression(self, value: str) -> Optional[str]:
        """从字符串中提取 JMESPath 表达式，要求前缀为 j:。"""
        if not isinstance(value, str):
            return None

        import re

        # Use DOTALL flag to allow . to match newlines, supporting multi-line expressions
        match = re.match(r"^[jJ]\s*:\s*(.+)$", value, re.DOTALL)
        if not match:
            return None

        expression = match.group(1).strip()
        return expression or None

    def _evaluate_jmes_expression(self, value: str, context_data: Dict[str, Any]) -> tuple[bool, Any]:
        """检测并执行带 `j:` 前缀的 JMESPath 表达式。"""
        import jmespath

        expression = self._extract_jmes_expression(value)
        if expression is None:
            return False, value

        try:
            result = jmespath.search(expression, context_data)
            return True, result
        except Exception as exc:
            logger.warning(f"JMESPath expression '{expression}' failed: {exc}")
            return True, None

    # 已移除：_resolve_simple_path（为提高严谨性，强制使用 j: 前缀的 JMESPath 表达式）

    def _resolve_inputs(self, inputs_config: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve inputs from step context based on configuration."""
        resolved = {}

        for input_name, input_spec in inputs_config.items():
            if isinstance(input_spec, str):
                # Simple reference: "node_id.output_key"
                if '.' in input_spec:
                    node_id, output_key = input_spec.split('.', 1)
                    if node_id in self.step_context:
                        resolved[input_name] = self.step_context[node_id].get(output_key)
                else:
                    # Direct step context reference
                    resolved[input_name] = self.step_context.get(input_spec)

            elif isinstance(input_spec, dict):
                # Complex input specification with default values, transforms, etc.
                source = input_spec.get('source')
                default = input_spec.get('default')

                if source and '.' in source:
                    node_id, output_key = source.split('.', 1)
                    if node_id in self.step_context:
                        resolved[input_name] = self.step_context[node_id].get(output_key, default)
                    else:
                        resolved[input_name] = default
                else:
                    resolved[input_name] = default

        return resolved

    def _serialize_operator_result(
        self,
        result: BaseOperatorResult,
        *,
        agent_identifier: Optional[str],
        operator_id: str,
        operator_name: Optional[str] = None,
        operator_type: Optional[str] = None,
        rendered_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """将 BaseOperatorResult 序列化为可供 JMESPath 查询的字典。

        新结构：name, type, inputs, output 在同一层级
        """
        identifier = agent_identifier or (result.metadata or {}).get("agent_id") or result.agent_id or "unknown"

        def _deepcopy(value: Any) -> Any:
            try:
                return copy.deepcopy(value)
            except Exception:
                return value

        # 提取 structured_output
        structured_output = None
        if hasattr(result, "structured_output") and getattr(result, "structured_output") is not None:
            structured_output = _deepcopy(getattr(result, "structured_output"))
        elif isinstance(result.value, dict):
            if "structured_output" in result.value and result.value["structured_output"] is not None:
                structured_output = _deepcopy(result.value["structured_output"])
            elif "results" in result.value:
                results_list = result.value.get("results") or []
                for item in results_list:
                    if not isinstance(item, dict):
                        continue
                    match_id = item.get("agent_id") or item.get("id")
                    if match_id == identifier:
                        candidate = item.get("result", {}).get("structured_output")
                        if candidate is not None:
                            structured_output = _deepcopy(candidate)
                            break

        # 确定 output 值
        output_value = structured_output if structured_output is not None else _deepcopy(result.value)
        raw_output_value = _deepcopy(result.value)

        # 提取额外字段
        extras: Dict[str, Any] = {}
        if hasattr(result, "performative_output"):
            extras["performative_output"] = _deepcopy(getattr(result, "performative_output"))
        if hasattr(result, "phases"):
            extras["phases"] = _deepcopy(getattr(result, "phases"))
        if hasattr(result, "actions_taken"):
            extras["actions_taken"] = _deepcopy(getattr(result, "actions_taken"))
        if hasattr(result, "fovs_used"):
            extras["fovs_used"] = _deepcopy(getattr(result, "fovs_used"))
        if hasattr(result, "total_turns"):
            extras["total_turns"] = getattr(result, "total_turns")

        # 🔧 新结构：name, type, inputs, output 在同一层级
        serialized: Dict[str, Any] = {
            "operator_id": operator_id,
            "name": operator_name or operator_id,  # operator 名称（如 rule/behavior name）
            "type": operator_type or "unknown",     # operator 类型
            "inputs": _deepcopy(rendered_params or {}),  # 渲染后的参数
            "output": output_value,                 # 主要输出
            "raw_output": raw_output_value,         # 原始输出（便于调试/兼容）
            "agent_id": identifier,
            "status": result.status,
            "execution_time": getattr(result, "execution_time", None),
            "error_message": getattr(result, "error_message", None),
            "metadata": _deepcopy(result.metadata or {}),
        }

        # 向后兼容：保留 result 和 value 字段
        serialized["result"] = output_value
        serialized["value"] = _deepcopy(result.value)

        if structured_output is not None:
            serialized["structured_output"] = structured_output
        if extras:
            serialized.update(extras)

        return serialized

    def _record_operator_result(
        self,
        *,
        node: StepNode,
        operator_config: OperatorConfig,
        agent_identifier: Optional[str],
        result: BaseOperatorResult,
        target_store: Optional[Dict[str, Any]] = None,
        rendered_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """序列化 operator 结果并写入本地上下文与 JMESPath 构建器。

        Args:
            rendered_params: 渲染后的参数（执行时的实际参数）
        """
        # 提取 operator 名称（rule/behavior name）
        operator_name = operator_config.params.get("name", operator_config.id)

        # 序列化结果，传入完整信息
        serialized = self._serialize_operator_result(
            result,
            agent_identifier=agent_identifier,
            operator_id=operator_config.id,
            operator_name=operator_name,
            operator_type=operator_config.type,
            rendered_params=rendered_params,
        )

        # 写入本地上下文
        if target_store is not None:
            target_store[operator_config.id] = serialized
            previous_version = target_store.get("__version__", 0)
            if not isinstance(previous_version, int):
                previous_version = 0
            target_store["__version__"] = previous_version + 1

        # 记录到 JMESPath 构建器
        self._context_builder.record_operator_result(
            node_id=node.id,
            operator_id=operator_config.id,
            operator_type=operator_config.type,
            description=operator_config.params.get("desc"),
            execution=serialized,
        )
        return serialized

    # Built-in selector implementations
    async def _all_agents_selector(self, params: Dict[str, Any], context: ExecutionContext) -> List['Agent']:
        """Select all agents."""
        return context.world.get_all_agents()

    async def _by_type_selector(self, params: Dict[str, Any], context: ExecutionContext) -> List['Agent']:
        """Select agents by social type."""
        agent_type = params.get('agent_type', '')
        return context.world.get_agents_by_type(agent_type)

    async def _by_archetype_selector(self, params: Dict[str, Any], context: ExecutionContext) -> List['Agent']:
        """Select agents by execution archetype."""
        archetype = params.get('archetype', '')
        return context.world.get_agents_by_archetype(archetype)

    async def _by_id_selector(self, params: Dict[str, Any], context: ExecutionContext) -> List['Agent']:
        """Select specific agents by ID."""
        agent_ids = params.get('agent_ids', [])
        if isinstance(agent_ids, str):
            agent_ids = [agent_ids]
        return [context.world.get_agent(aid) for aid in agent_ids if aid in context.world.agents_data]

    async def _environment_selector(self, params: Dict[str, Any], context: ExecutionContext) -> List['Agent']:
        """Special selector for environment operations - returns environment as special target."""
        # Return the environment wrapped in a list for global mode detection
        environment = context.world.get_environment()
        return [environment]

    # Built-in operator implementations
    def _create_behavior_operator(self, behavior_name: str):
        """
        创建behavior operator的工厂方法

        Behavior operator的设计原则：
        1. 支持V2并行Agent模式 - 每个agent独立执行behavior
        2. 提供完整的ExecutionContext - 包含world、step、node等信息
        3. 智能参数处理 - 支持input_mapping和JMESPath参数解析
        4. 灵活返回值处理 - 支持Dict或BaseOperatorResult格式
        5. 完整的错误处理和日志记录
        """
        async def behavior_operator(agents: List['Agent'], params: Dict[str, Any], context: ExecutionContext) -> BaseOperatorResult:
            import time
            start_time = time.time()

            # 验证behavior是否已注册
            resolved = self._resolve_logic_entry(behavior_name, "behavior")
            if not resolved:
                error_msg = f"Behavior '{behavior_name}' not found in registry"
                logger.error(error_msg)
                return BaseOperatorResult(
                    agent_id="batch" if len(agents) > 1 else (agents[0].id if agents else "none"),
                    status="error",
                    value=None,
                    error_message=error_msg,
                    execution_time=time.time() - start_time,
                    metadata={"behavior_name": behavior_name}
                )

            resolved_name, behavior_info = resolved
            behavior_func = behavior_info['function']
            behavior_sig = behavior_info.get('signature', inspect.signature(behavior_func))
            sync_fields = self._resolve_behavior_sync_fields(behavior_info, params)

            # 保留字段（不传入 behavior 函数）
            reserved_behavior_keys = {'id', 'type', 'name', 'desc', 'state_sync_fields'}

            # 🔧 新增：智能参数映射逻辑（与 rule_operator 类似）
            # 1. 识别函数签名中的参数
            has_var_keyword = False
            context_param_name = None
            explicit_params = set()

            for param_name, param in behavior_sig.parameters.items():
                if param_name in ['self', 'agent', 'env']:
                    continue  # 跳过固定参数

                # 检测 **kwargs
                if param.kind == inspect.Parameter.VAR_KEYWORD:
                    has_var_keyword = True
                    continue

                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    continue  # *args

                # 检测 ExecutionContext
                if param.annotation != inspect.Parameter.empty:
                    annotation_str = str(param.annotation)
                    if 'ExecutionContext' in annotation_str or param_name == 'context':
                        context_param_name = param_name
                        continue

                # 记录显式参数名
                explicit_params.add(param_name)

            # 2. 准备基础参数（移除保留字段）
            base_behavior_params = {
                k: v for k, v in params.items()
                if k not in reserved_behavior_keys
            }

            results = []

            # 为每个agent执行behavior（支持V2并行模式）
            for agent in agents:
                try:
                    # 创建environment proxy
                    env = context.world.get_environment()

                    # 准备behavior参数
                    behavior_params = dict(base_behavior_params)

                    logger.debug(
                        f"Executing behavior '{resolved_name}' for agent {agent.id} "
                        f"(has_var_keyword={has_var_keyword}, explicit_params={explicit_params})"
                    )

                    # 导入context管理工具
                    from .context_stack import behavior_context

                    # 获取当前上下文栈
                    current_stack = context.world.get_context_stack()

                    # 在behavior上下文中执行函数
                    with behavior_context(current_stack, behavior_name, params=behavior_params) as new_stack:
                        # 更新World中的当前上下文栈，确保状态变更事件包含正确的上下文
                        context.world.set_context_stack(new_stack)

                        try:
                            # 3. 智能参数准备
                            if has_var_keyword:
                                # 如果有 **kwargs，传递所有参数（它们会被函数接收）
                                call_kwargs = dict(behavior_params)
                                if unmapped := {k: v for k, v in behavior_params.items() if k not in explicit_params}:
                                    logger.debug(
                                        f"Behavior '{resolved_name}' has **kwargs, passing {len(unmapped)} "
                                        f"unmapped params: {list(unmapped.keys())}"
                                    )
                            else:
                                # 如果没有 **kwargs，只传递显式声明的参数
                                call_kwargs = {
                                    k: v for k, v in behavior_params.items()
                                    if k in explicit_params
                                }
                                # 添加缺失的默认值参数
                                for param_name, param in behavior_sig.parameters.items():
                                    if param_name not in call_kwargs and param_name in explicit_params:
                                        if param.default != inspect.Parameter.empty:
                                            call_kwargs[param_name] = param.default

                            # 注入 ExecutionContext
                            if context_param_name:
                                call_kwargs[context_param_name] = context

                            # 执行behavior函数 - 统一接口: (agent, env, **kwargs)
                            result = await invoke_maybe_async(behavior_func, agent, env, **call_kwargs)
                        finally:
                            # 始终恢复原始上下文栈（在with退出前恢复）
                            context.world.set_context_stack(current_stack)

                    # 处理返回结果
                    if isinstance(result, BaseOperatorResult):
                        # 如果返回BaseOperatorResult，直接使用
                        behavior_result = result
                    elif isinstance(result, dict):
                        # 如果返回字典，包装为BaseOperatorResult
                        behavior_result = BaseOperatorResult(
                            agent_id=agent.id,
                            status="success",
                            value=result,
                            execution_time=time.time() - start_time,
                            metadata={
                                "behavior_name": behavior_name,
                                "behavior_type": "dict_return"
                            }
                        )
                    else:
                        # 其他类型的返回值
                        behavior_result = BaseOperatorResult(
                            agent_id=agent.id,
                            status="success",
                            value={"result": result},
                            execution_time=time.time() - start_time,
                            metadata={
                                "behavior_name": behavior_name,
                                "behavior_type": type(result).__name__
                            }
                        )

                    results.append({
                        "target": agent.id,
                        "behavior": behavior_name,
                        "result": behavior_result,
                        "status": "success"
                    })

                    if sync_fields:
                        try:
                            self._verify_agent_state_sync(agent, resolved_name, sync_fields, context)
                        except Exception as sync_error:
                            logger.error(
                                "State sync verification failed for agent '%s' in behavior '%s': %s",
                                agent.id,
                                resolved_name,
                                sync_error
                            )

                    logger.debug(f"Behavior '{behavior_name}' completed successfully for agent {agent.id}")

                except Exception as e:
                    logger.error(f"Error executing behavior '{behavior_name}' for agent {agent.id}: {e}")

                    error_result = BaseOperatorResult(
                        agent_id=agent.id,
                        status="error",
                        value=None,
                        error_message=str(e),
                        execution_time=time.time() - start_time,
                        metadata={"behavior_name": behavior_name, "error_type": type(e).__name__}
                    )

                    results.append({
                        "target": agent.id,
                        "behavior": behavior_name,
                        "result": error_result,
                        "status": "error"
                    })

            # 汇总执行结果
            successful_results = [r for r in results if r["status"] == "success"]
            error_results = [r for r in results if r["status"] == "error"]

            # 确定整体状态
            if len(error_results) == 0:
                overall_status = "success"
            elif len(successful_results) == 0:
                overall_status = "error"
            else:
                overall_status = "partial"

            # 创建汇总结果
            execution_time = time.time() - start_time

            # 如果只有一个agent，返回该agent的具体结果
            if len(agents) == 1 and len(results) == 1:
                single_result = results[0]["result"]
                single_result.execution_time = execution_time
                return single_result

            # 多个agent的情况，返回汇总结果
            summary_value = {
                "behavior_name": behavior_name,
                "total_agents": len(agents),
                "successful_executions": len(successful_results),
                "failed_executions": len(error_results),
                "execution_results": results
            }

            return BaseOperatorResult(
                agent_id="batch",
                status=overall_status,
                value=summary_value,
                execution_time=execution_time,
                metadata={
                    "behavior_name": behavior_name,
                    "agents_processed": len(agents),
                    "success_count": len(successful_results),
                    "error_count": len(error_results)
                }
            )

        return behavior_operator

    async def _instruct_operator(self, agents: List['Agent'], params: Dict[str, Any], context: ExecutionContext) -> InstructOperatorResult:
        """Pure task dispatcher for instruction execution.

        Delegates all business logic to World.instruct_agent() and simply
        coordinates the results. This operator is now a simple dispatcher
        that maintains the interface but moves complexity to World.

        Returns InstructOperatorResult for each agent processed.
        """
        import time

        # Parse parameters
        instruction = params.get('instruction', '')
        if not instruction:
            logger.warning("No instruction provided for instruct operator")
            return InstructOperatorResult(
                agent_id="batch",
                status="error",
                value={"error": "No instruction provided", "agent_count": 0},
                error_message="No instruction provided"
            )

        fovs = params.get('fovs', [])
        action_tags = params.get('action_tags', [])
        extra_notes = params.get("extra_notes") or []
        # 根据输出/记忆需求补充附加说明
        notes_from_params: List[str] = []
        if params.get("output_schema"):
            notes_from_params.append("请调用 finish_instruction/submit_result，并按配置的 JSON Schema 返回结果。")
        if params.get("save_memory"):
            if params.get("extract_memory", True):
                notes_from_params.append("回答会被提取成记忆，请用第一人称描述你做了什么、结果和感受。")
            else:
                notes_from_params.append("回答将直接写入记忆，请确保内容真实、可回溯。")
        else:
            notes_from_params.append("本次回答不会写入记忆，可直接作答。")
        if notes_from_params:
            extra_notes = list(extra_notes) + notes_from_params

        # Extract only known parameters that instruct_agent accepts
        # Avoid passing operator-specific params like 'type', 'id', etc.
        KNOWN_INSTRUCT_PARAMS = {
            "instruction",
            "fovs",
            "action_tags",
            "output_schema",
            "is_memory",
            "reasoning_stages",
            "extract_memory",
            "save_memory",
            "retrieve_memory",
            "max_turns",
            "extra_notes",
        }

        kwargs = {k: v for k, v in params.items()
                 if k in KNOWN_INSTRUCT_PARAMS and k not in ['instruction', 'fovs', 'action_tags']}
        if extra_notes:
            kwargs["extra_notes"] = extra_notes

        start_time = time.time()
        results = []
        successful_agents = []

        operator_model_id = params.get('model') if isinstance(params.get('model'), str) else None

        # Loop through agents and delegate to World.instruct_agent
        for agent in agents:
            if agent.archetype != 'llm':
                logger.debug(f"Skipping non-LLM agent {agent.id}")
                continue

            # Delegate to World.instruct_agent for all business logic
            try:
                execution_result = await context.world.instruct_agent(
                    agent_id=agent.id,
                    instruction=instruction,
                    fovs=fovs,
                    action_tags=action_tags,
                    current_step=context.world.step,
                    model_id=operator_model_id,
                    **kwargs
                )

                results.append({
                    "agent_id": agent.id,
                    "status": execution_result.get("status", "success"),
                    "result": execution_result
                })
                successful_agents.append(agent.id)

                # Log the instruction execution
                context.log_event("instruct_execution", source=f"schedule.instruct_operator", data={
                    "agent_id": agent.id,
                    "instruction": instruction,
                    "action_tags": action_tags,
                    "fovs_used": fovs,
                    "has_structured_output": execution_result.get("structured_output") is not None,
                    "finish_instruction_called": execution_result.get("finish_instruction_called", False),
                    "model_id": execution_result.get("model_id")
                })

                logger.info(f"Instructed agent {agent.id} via World.instruct_agent (fovs={fovs})")

            except Exception as e:
                logger.error(f"Error executing instruction for agent {agent.id}: {e}")
                results.append({
                    "agent_id": agent.id,
                    "status": "error",
                    "error": str(e)
                })

        execution_time = time.time() - start_time

        # Create consolidated result
        return InstructOperatorResult(
            agent_id="batch" if len(agents) > 1 else (agents[0].id if agents else "none"),
            status="success" if successful_agents else "error",
            value={
                "instruction": instruction,
                "action_tags": action_tags,
                "fovs_requested": fovs,
                "results": results,
                "successful_agents": successful_agents,
                "agent_count": len(successful_agents)
            },
            execution_time=execution_time,
            fovs_used=fovs,
            metadata={
                "total_agents_processed": len(agents),
                "llm_agents_found": len([a for a in agents if a.archetype == 'llm']),
                "successful_count": len(successful_agents)
            }
        )

    async def _interview_operator(self, agents: List['Agent'], params: Dict[str, Any], context: ExecutionContext) -> InstructOperatorResult:
        """Pure task dispatcher for interview execution.

        Delegates all business logic to World.interview_agent() and simply
        coordinates the results. This operator is specialized for conducting
        interviews without polluting agent memory or allowing actions.

        Returns InstructOperatorResult for each agent processed.
        """
        import time

        # Parse parameters (using 'question' instead of 'instruction' for semantic clarity)
        question = params.get('question', params.get('instruction', ''))
        if not question:
            logger.warning("No question provided for interview operator")
            return InstructOperatorResult(
                agent_id="batch",
                status="error",
                value={"error": "No question provided", "agent_count": 0},
                error_message="No question provided"
            )

        fovs = params.get('fovs', [])
        extra_notes = params.get("extra_notes") or []
        if params.get("output_schema"):
            extra_notes = list(extra_notes) + ["请调用 finish_instruction/submit_result，并按配置的 JSON Schema 返回结果。"]

        # Extract only known parameters that interview_agent accepts
        # Note: No action_tags for interview - they are hardcoded to [] in interview_agent
        KNOWN_INTERVIEW_PARAMS = {
            "question", "instruction", "fovs", "output_schema", "reasoning_stages", "extra_notes"
        }

        kwargs = {k: v for k, v in params.items()
                 if k in KNOWN_INTERVIEW_PARAMS and k not in ['question', 'instruction', 'fovs']}
        if extra_notes:
            kwargs["extra_notes"] = extra_notes

        start_time = time.time()
        results = []
        successful_agents = []

        operator_model_id = params.get('model') if isinstance(params.get('model'), str) else None

        # Loop through agents and delegate to World.interview_agent
        for agent in agents:
            if agent.archetype != 'llm':
                logger.debug(f"Skipping non-LLM agent {agent.id}")
                continue

            # Delegate to World.interview_agent for all business logic
            try:
                execution_result = await context.world.interview_agent(
                    agent_id=agent.id,
                    question=question,
                    fovs=fovs,
                    current_step=context.world.step,
                    model_id=operator_model_id,
                    **kwargs
                )

                results.append({
                    "agent_id": agent.id,
                    "status": execution_result.get("status", "success"),
                    "result": execution_result
                })
                successful_agents.append(agent.id)

                # Log the interview execution
                context.log_event("interview_execution", source=f"schedule.interview_operator", data={
                    "agent_id": agent.id,
                    "question": question,
                    "fovs_used": fovs,
                    "has_structured_output": execution_result.get("structured_output") is not None,
                    "finish_instruction_called": execution_result.get("finish_instruction_called", False),
                    "memory_retrieved": execution_result.get("memory_retrieved", True),
                    "memory_saved": execution_result.get("memory_saved", False),
                    "model_id": execution_result.get("model_id")
                })

                logger.info(f"Interviewed agent {agent.id} via World.interview_agent (fovs={fovs})")

                # 简化的访谈结果记录
                try:
                    pm = getattr(context.world, "_persistence_manager", None)
                    if pm and hasattr(pm, "save_interview_record"):
                        record = {
                            "agent_id": agent.id,
                            "status": execution_result.get("status", "success"),
                        }
                        step_no = context.step_number
                        node_id = context.node.id if context.node else "unknown"
                        operator_id = context.operator_id or "interview"
                        structured = execution_result.get("structured_output")
                        if structured is not None:
                            record["structured_output"] = structured
                        raw_output = execution_result.get("raw_output")
                        if raw_output is not None:
                            cleaned_output = copy.deepcopy(raw_output)
                            if isinstance(cleaned_output, dict):
                                cleaned_output.pop("full_history", None)
                            record["result"] = cleaned_output
                        pm.save_interview_record(step_no, node_id, operator_id, record)
                except Exception as log_exc:
                    logger.warning(f"Failed to persist interview record for agent {agent.id}: {log_exc}")

            except Exception as e:
                logger.error(f"Error executing interview for agent {agent.id}: {e}")
                results.append({
                    "agent_id": agent.id,
                    "status": "error",
                    "error": str(e)
                })

        execution_time = time.time() - start_time

        # Create consolidated result
        return InstructOperatorResult(
            agent_id="batch" if len(agents) > 1 else (agents[0].id if agents else "none"),
            status="success" if successful_agents else "error",
            value={
                "question": question,
                "fovs_requested": fovs,
                "results": results,
                "successful_agents": successful_agents,
                "agent_count": len(successful_agents),
                "interview_mode": True  # 标记这是访谈模式
            },
            execution_time=execution_time,
            fovs_used=fovs,
            metadata={
                "total_agents_processed": len(agents),
                "llm_agents_found": len([a for a in agents if a.archetype == 'llm']),
                "successful_count": len(successful_agents),
                "operator_type": "interview"
            }
        )

    def _create_rule_operator(self, rule_name: str) -> Operator:
        """Create a rule operator with intelligent parameter mapping.

        根据设计文档实现智能参数映射：
        1. 查找 rule 函数及其签名
        2. 创建包装器函数，自动映射参数
        3. 支持默认值和必需参数验证
        4. 支持 **kwargs 参数打包（将未匹配的参数传入）

        ⚠️ 重要变更：rule 的第一个参数现在是 env (Environment) 而不是 world

        Args:
            rule_name: 规则函数的名称

        Returns:
            封装了智能参数映射的 operator 函数
        """
        import inspect

        async def rule_operator(agents: List['Agent'], params: Dict[str, Any], context: ExecutionContext) -> BaseOperatorResult:
            """智能规则操作符 - 具备参数映射功能"""

            # 1. 查找规则函数
            resolved = self._resolve_logic_entry(rule_name, "rule")
            if not resolved:
                error_msg = f"Rule function '{rule_name}' not found in registry"
                logger.error(error_msg)
                return BaseOperatorResult(
                    agent_id="environment",
                    status="error",
                    value={"error": error_msg},
                    error_message=error_msg
                )

            resolved_name, rule_info = resolved
            rule_func = rule_info['function']
            rule_sig = rule_info['signature']

            # 2. 智能参数映射
            mapped_args = {}
            has_var_keyword = False  # 检测是否有 **kwargs
            context_param_name = None  # ExecutionContext 参数名

            # 遍历函数签名中的所有参数（跳过 env 和 self）
            for param_name, param in rule_sig.parameters.items():
                if param_name in ['self', 'env']:
                    continue  # 跳过特殊参数

                # 检测 **kwargs
                if param.kind == inspect.Parameter.VAR_KEYWORD:
                    has_var_keyword = True
                    continue  # 暂时跳过，稍后处理

                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    continue  # *args 不纳入必需参数校验

                # 检测 ExecutionContext 类型参数（通过类型注解或参数名）
                if param.annotation != inspect.Parameter.empty:
                    annotation_str = str(param.annotation)
                    if 'ExecutionContext' in annotation_str or param_name == 'context':
                        context_param_name = param_name
                        continue  # context 参数由系统注入，不从 params 获取

                if param_name in params:
                    # 参数在 YAML 中提供了值
                    mapped_args[param_name] = params[param_name]
                elif param.default != inspect.Parameter.empty:
                    # 参数有默认值
                    mapped_args[param_name] = param.default
                else:
                    # 必需参数缺失
                    error_msg = f"Required parameter '{param_name}' missing for rule '{rule_name}'"
                    logger.error(error_msg)
                    return BaseOperatorResult(
                        agent_id="environment",
                        status="error",
                        value={"error": error_msg, "missing_param": param_name},
                        error_message=error_msg
                    )

            # 3. 如果函数有 **kwargs，将所有未匹配的参数打包传入
            if has_var_keyword:
                # 找出所有已处理的参数名（包括特殊参数和已映射参数）
                handled_param_names = set(rule_sig.parameters.keys())
                handled_param_names.update(['self', 'env'])
                if context_param_name:
                    handled_param_names.add(context_param_name)

                # 将所有未处理的 params 打包进 mapped_args
                unmapped_params = {
                    k: v for k, v in params.items()
                    if k not in mapped_args  # 尚未映射的参数
                }
                mapped_args.update(unmapped_params)

                if unmapped_params:
                    logger.debug(
                        f"Rule '{rule_name}' has **kwargs, passing {len(unmapped_params)} unmapped params: "
                        f"{list(unmapped_params.keys())}"
                    )

            # 4. 调用规则函数
            try:
                import time
                start_time = time.time()

                # 准备调用参数：env + mapped_args
                env = context.world.get_environment()
                call_args = dict(mapped_args)

                # 注入 ExecutionContext（如果函数签名中有）
                if context_param_name:
                    call_args[context_param_name] = context

                # 调用用户定义的规则函数
                result = await invoke_maybe_async(rule_func, env, **call_args)

                execution_time = time.time() - start_time

                # 5. 记录执行日志
                context.log_event("rule_execution", source=f"schedule.rule_operator", data={
                    "rule_name": rule_name,
                    "mapped_args": {k: v for k, v in call_args.items() if k != context_param_name},  # 排除 context
                    "has_var_keyword": has_var_keyword,
                    "execution_time": execution_time,
                    "result_type": type(result).__name__
                })

                logger.info(f"Executed rule '{resolved_name}' with args: {list(call_args.keys())}")

                # 6. 返回结果
                return BaseOperatorResult(
                    agent_id="environment",
                    status="success",
                    value=result,
                    execution_time=execution_time,
                    metadata={
                        "rule_function": resolved_name,
                        "rule_name": resolved_name,
                        "params_provided": len(params),
                        "params_mapped": len(mapped_args),
                        "has_var_keyword": has_var_keyword,
                        "mapped_args": {k: v for k, v in call_args.items() if k != context_param_name}
                    }
                )

            except Exception as e:
                error_msg = f"Error executing rule '{rule_name}': {str(e)}"
                logger.error(error_msg)
                return BaseOperatorResult(
                    agent_id="environment",
                    status="error",
                    value={"error": error_msg, "rule_name": rule_name},
                    error_message=error_msg
                )

        return rule_operator

    def _resolve_fov_entry(self, name: str) -> Optional[Dict[str, Any]]:
        """解析 FoV 引用，支持 canonical id 与短名兼容。"""

        if name in self.registry.env_fovs:
            return self.registry.env_fovs[name]

        if isinstance(name, str) and name:
            short_name = name.rsplit('.', maxsplit=1)[-1]
            if short_name != name and short_name in self.registry.env_fovs:
                return self.registry.env_fovs[short_name]

        return None

    def _resolve_logic_entry(self, name: str, kind: str) -> Optional[tuple[str, Dict[str, Any]]]:
        """解析 rule/behavior 引用，兼容 canonical id。"""

        table = self.registry.rules if kind == "rule" else self.registry.behaviors
        if isinstance(name, str):
            normalized = name.strip()
        else:
            normalized = name

        if normalized in table:
            return normalized, table[normalized]

        return None

    def _resolve_behavior_sync_fields(self, behavior_info: Dict[str, Any], operator_params: Dict[str, Any]) -> List[str]:
        """根据 metadata 与配置提取需要核验的状态字段名称。"""
        fields: List[str] = []

        meta = behavior_info.get('meta')
        if meta and getattr(meta, 'state_access_declaration', None):
            writes = meta.state_access_declaration.get('writes') or []
            if isinstance(writes, list):
                fields.extend(str(item) for item in writes if isinstance(item, str))

        config_fields = operator_params.get('state_sync_fields')
        if isinstance(config_fields, (list, tuple, set)):
            fields.extend(str(item) for item in config_fields if isinstance(item, str))
        elif isinstance(config_fields, str):
            fields.append(config_fields)

        # 移除重复字段，保持顺序
        seen = set()
        deduped: List[str] = []
        for name in fields:
            if name not in seen:
                seen.add(name)
                deduped.append(name)
        return deduped

    def _verify_agent_state_sync(self, agent: 'Agent', behavior_name: str, field_names: List[str], context: ExecutionContext) -> None:
        """在行为执行结束后校验声明的状态字段是否已写回 World。"""
        if not field_names:
            return

        agent_state = context.world.agents_data.get(agent.id, {}).get("state", {})
        if not isinstance(agent_state, dict):
            logger.warning(
                "State sync skipped: agent '%s' state is not a dict when verifying behavior '%s'",
                agent.id,
                behavior_name
            )
            return

        missing = [field for field in field_names if field not in agent_state]
        if missing:
            logger.warning(
                "State sync warning: behavior '%s' declares writes %s, but agent '%s' state is missing %s",
                behavior_name,
                field_names,
                agent.id,
                missing
            )

    def _call_fov_function(self, fov_name: str, agent: 'Agent', environment) -> str:
        """Call FoV function using unified registry lookup with standardized (agent, env) signature."""
        entry = self._resolve_fov_entry(fov_name)
        if entry:
            return entry['function'](agent, environment)

        # Then try environment methods for backward compatibility
        if hasattr(environment, fov_name):
            env_method = getattr(environment, fov_name)
            return env_method(agent, environment)

        else:
            print(type(environment), dir(environment))
            raise ValueError(f"FoV function '{fov_name}' not found in registry or environment methods. {type(environment)}, dir: {dir(environment)}")

    # Built-in converter implementations
    async def _jmespath_converter(self, operator_results: List[BaseOperatorResult], params: Dict[str, Any], context: ExecutionContext, world: 'World') -> Dict[str, Any]:
        """Universal JMESPath-based converter with 'God View' world state access.

        This enhanced converter provides access to:
        - results: Original operator results
        - world: Complete world state (agents, environment, etc.)
        - inputs: Upstream node inputs
        - step_context: Current step context

        Examples:
          expression: "results_map.metrics_analysis[0].value"             # Access rule output by operator id
          expression: "avg(world.agents_data.*.state.balance)"            # Average balance across agents
          expression: "{success: length(results[?status=='success']), failures: length(results[?status=='error'])}"
        """
        import jmespath
        import re

        raw_expression = params.get('expression', 'j:@')  # Default to identity
        expression = self._extract_jmes_expression(raw_expression)
        print(f"🔄 [CONVERTER DEBUG] JMESPath converter called")
        print(f"🔄 [CONVERTER DEBUG] Expression (raw): {raw_expression}")
        print(f"🔄 [CONVERTER DEBUG] Expression (parsed): {expression}")
        print(f"🔄 [CONVERTER DEBUG] Operator results count: {len(operator_results)}")
        print(f"🔄 [CONVERTER DEBUG] World available: {world is not None}")
        if world:
            print(f"🔄 [CONVERTER DEBUG] World step: {world.step}")
            print(f"🔄 [CONVERTER DEBUG] World agents count: {len(world.agents_data)}")
            print(f"🔄 [CONVERTER DEBUG] World agents: {list(world.agents_data.keys())}")

        if expression is None:
            logger.warning("JMESPath converter expression 缺少 'j:' 前缀或为空，已跳过执行")
            return {}

        try:
            if expression:
                referenced_indices = [
                    int(match.group(1))
                    for match in re.finditer(r"results\[(\d+)\]", expression)
                ]
                if referenced_indices:
                    max_index = max(referenced_indices)
                    if max_index >= len(operator_results):
                        raise ValueError(
                            f"JMESPath 表达式引用了不存在的 results[{max_index}]，"
                            f"当前仅有 {len(operator_results)} 个算子结果。"
                        )

            # Convert BaseOperatorResult objects to dictionaries for JMESPath processing
            results_data = []
            results_map: Dict[str, List[Dict[str, Any]]] = {}
            for result in operator_results:
                result_dict = {
                    "agent_id": result.agent_id,
                    "status": result.status,
                    "value": result.value,
                    "error_message": result.error_message,
                    "execution_time": result.execution_time,
                    "metadata": result.metadata
                }
                results_data.append(result_dict)
                operator_id = (result.metadata or {}).get("operator_id")
                if operator_id:
                    results_map.setdefault(operator_id, []).append(result_dict)

            print(f"🔄 [CONVERTER DEBUG] Converted {len(results_data)} operator results to dict format")

            # 使用 Builder 构建统一的 JMESPath 根上下文，并生成可写视图承载运行期字段
            root_context = self._context_builder.build_root(world=world, step_context=self.step_context)
            jmespath_data = dict(root_context)
            jmespath_data["results"] = results_data
            jmespath_data["results_map"] = results_map
            jmespath_data["inputs"] = getattr(context.node, 'inputs', {}) if context.node else {}
            jmespath_data["current_node_id"] = context.node.id if context.node else None
            if context.node:
                jmespath_data["results_order"] = [cfg.id for cfg in context.node.operator_configs]
            if "operators" in jmespath_data:
                jmespath_data["operators_map"] = jmespath_data["operators"]

            print(f"🔄 [CONVERTER DEBUG] Built JMESPath data context:")
            print(f"🔄 [CONVERTER DEBUG] - results: {len(jmespath_data['results'])} items")
            world_snapshot = jmespath_data.get("world", {})
            print(f"🔄 [CONVERTER DEBUG] - world.agents_data: {len(world_snapshot.get('agents_data', {}))} agents")
            print(f"🔄 [CONVERTER DEBUG] - world.step: {world_snapshot.get('step')}")
            print(f"🔄 [CONVERTER DEBUG] - inputs: {list(jmespath_data['inputs'].keys())}")
            step_context_snapshot = jmespath_data.get("step_context", {})
            print(f"🔄 [CONVERTER DEBUG] - step_context: {list(step_context_snapshot.keys())}")

            print(f"🔄 [CONVERTER DEBUG] Executing JMESPath expression...")

            transformed_data = jmespath.search(expression, jmespath_data)
            print(f"✅ [CONVERTER DEBUG] JMESPath execution successful!")
            print(f"✅ [CONVERTER DEBUG] Result type: {type(transformed_data)}")
            print(f"✅ [CONVERTER DEBUG] Result: {transformed_data}")

            return {
                "jmespath_result": transformed_data,
                "expression": expression,
                "data_sources": {
                    "results_count": len(results_data),
                    "agents_count": len(world.agents_data),
                    "world_step": world.step,
                    "inputs_available": list(getattr(context.node, 'inputs', {}).keys()) if context.node else [],
                    "step_context_nodes": list(self.step_context.keys())
                }
            }

        except Exception as e:
            print(f"❌ [CONVERTER DEBUG] JMESPath converter error: {e}")
            print(f"❌ [CONVERTER DEBUG] Error type: {type(e)}")
            import traceback
            print(f"❌ [CONVERTER DEBUG] Traceback:")
            traceback.print_exc()
            logger.error(f"JMESPath converter error with expression '{expression}': {e}")
            return {
                "jmespath_error": str(e),
                "expression": expression,
                "raw_results": len(operator_results),
                "world_available": world is not None,
                "world_step": getattr(world, 'step', 'unknown') if world else 'no_world'
            }

    async def _summary_converter(self, operator_results: List[BaseOperatorResult], params: Dict[str, Any], context: ExecutionContext, world: 'World') -> Dict[str, Any]:
        """Create a summary of operator results."""
        total_operators = len(operator_results)
        success_count = len([r for r in operator_results if r.status == "success"])
        error_count = len([r for r in operator_results if r.status == "error"])
        partial_count = len([r for r in operator_results if r.status == "partial"])

        # Calculate average execution time
        execution_times = [r.execution_time for r in operator_results if r.execution_time is not None]
        avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0

        summary = {
            "total_operators": total_operators,
            "success_count": success_count,
            "error_count": error_count,
            "partial_count": partial_count,
            "success_rate": success_count / total_operators if total_operators > 0 else 0,
            "avg_execution_time": avg_execution_time,
            "timestamp": context.world.step,
            "agent_ids": list(set(r.agent_id for r in operator_results))
        }

        return summary

    async def _passthrough_converter(self, operator_results: List[BaseOperatorResult], params: Dict[str, Any], context: ExecutionContext, world: 'World') -> Dict[str, Any]:
        """Pass through operator results as structured data."""
        return {
            "operator_results": [
                {
                    "agent_id": r.agent_id,
                    "status": r.status,
                    "value": r.value,
                    "error_message": r.error_message,
                    "execution_time": r.execution_time,
                    "metadata": r.metadata
                }
                for r in operator_results
            ],
            "passthrough": True,
            "count": len(operator_results)
        }


class Schedule:
    """The centralized plan manager of the simulation.

    Now acts as a plan manager that holds StepFlow instances.
    Responsibilities:
    - Compile schedule configuration into StepFlow instances
    - Coordinate multi-step execution
    - Provide unified interface for simulation engine
    """

    def __init__(
        self,
        schedule_config: Dict[str, Any],
        function_registry: 'FunctionRegistry',
        *,
        log_context: Optional[ExperimentLogContext] = None,
    ):
        """Initialize Schedule with configuration and compile StepFlow instances.

        Args:
            schedule_config: Configuration dictionary for the schedule
            function_registry: Registry containing all registered functions
        """
        self.config = schedule_config
        self.registry = function_registry
        self.log_context = log_context
        self.step_flows: List[StepFlow] = []

        # Compile configuration into StepFlow instances
        self._compile_schedule()

    def _compile_schedule(self) -> None:
        """Compile schedule configuration into StepFlow instances."""
        print(f"🏗️ [SCHEDULE DEBUG] Starting schedule compilation...")
        print(f"🏗️ [SCHEDULE DEBUG] Schedule config keys: {list(self.config.keys())}")

        # For simplicity, create a single StepFlow with all nodes
        # In full implementation, this could create multiple StepFlows for different phases

        step_flow = StepFlow(0, self.config, self.registry, log_context=self.log_context)
        self.step_flows.append(step_flow)

        print(f"🏗️ [SCHEDULE DEBUG] Created StepFlow with {len(step_flow.step_nodes)} nodes")
        print(f"🏗️ [SCHEDULE DEBUG] StepFlow node IDs: {[node.id for node in step_flow.step_nodes]}")
        logger.info(f"Compiled schedule with {len(step_flow.step_nodes)} nodes")

    async def execute_step(self, world: World, event_logger: Optional['EventLogger'] = None) -> Dict[str, Any]:
        """Execute one complete simulation step using StepFlow.

        Args:
            world: Current world state to modify directly
            event_logger: Optional event logger for tracking events

        Returns:
            Execution results and metrics
        """
        print(f"🎬 [SCHEDULE DEBUG] Starting step execution for world step {world.step}")
        print(f"🎬 [SCHEDULE DEBUG] Available step flows: {len(self.step_flows)}")
        print(f"🎬 [SCHEDULE DEBUG] World has {len(world.agents_data)} agents")
        logger.info(f"Executing step {world.step} with {len(self.step_flows)} step flows")

        # Inject function registry into world for FoV execution
        world.set_function_registry(self.registry)
        print(f"🎬 [SCHEDULE DEBUG] Function registry injected into world")

        # Create execution context with event logger
        from .transaction import EventLogger
        context = ExecutionContext(
            world=world,
            step=None,  # Will be set by StepFlow
            node=None,  # Will be set by individual nodes
            caller=self,
            event_logger=event_logger,
            log_context=self.log_context,
        )
        print(f"🎬 [SCHEDULE DEBUG] Execution context created")

        # Execute the first (and currently only) StepFlow
        if self.step_flows:
            step_flow = self.step_flows[0]
            print(f"🎬 [SCHEDULE DEBUG] Executing StepFlow with {len(step_flow.step_nodes)} nodes")
            result = await step_flow.execute(world, context)
            print(f"🎬 [SCHEDULE DEBUG] StepFlow execution completed successfully!")
            print(f"🎬 [SCHEDULE DEBUG] Result keys: {list(result.keys())}")
            logger.info("Step execution completed")
            return result
        else:
            print(f"⚠️ [SCHEDULE DEBUG] No step flows configured!")
            logger.warning("No step flows configured")
            return {"step_number": world.step, "nodes_executed": 0}
