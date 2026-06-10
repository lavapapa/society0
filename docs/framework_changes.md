# 提案：修改 SimEngine 以支持自定义环境

本文档详细说明了为 `sim_engine.py` 提出的修改，旨在使引擎能够发现和实例化自定义的环境类，例如我们正在构建的 `SocialNetworkEnv`。

## 核心目标

1.  **自动发现内置环境**: 引擎应能自动识别出 `simengine.env` 包中提供的所有内置环境。
2.  **支持用户自定义环境**: 引擎应提供一个简单的方法，让库的使用者可以注册他们自己开发的环境类。
3.  **根据配置实例化**: 引擎在初始化时，能够根据配置文件中的 `type` 字段，创建正确类型的环境实例。

## 修改方案

我们将采用您提议的、在 `simengine.env.__init__.py` 中手动注册内置环境的简洁方案。为配合该方案，需要对 `SimEngine` 类进行以下三处最小化的修改。

### 1. 修改 `SimEngine.__init__` 方法

**目的**: 在引擎实例化时，增加一个环境类的注册表，并从 `simengine.env` 包中自动加载所有内置环境。

**修改前:**
```python
# in SimEngine.__init__
self.save_dir = save_dir
self.config = self._load_and_merge_configs(base_config, kwargs_configs)

self.registries: List[FunctionRegistry] = []
self.persistence_manager = PersistenceManager(save_dir)
self.event_logger = EventLogger()
self.schedule: Optional[Schedule] = None
self.current_world_state: Optional[WorldState] = None

self._status = "NEW"  # NEW, RESUMED
self.is_initialized = False
```

**修改后:**
```python
# in SimEngine.__init__
import simengine.env # 触发 env 包的加载和自动注册
from simengine.env import BUILTIN_ENVS

# ...
self.save_dir = save_dir
self.config = self._load_and_merge_configs(base_config, kwargs_configs)

# --- 环境类注册表 ---
# 从内置注册表开始，允许用户后续添加更多
self.env_classes: Dict[str, Type[Environment]] = BUILTIN_ENVS.copy()

self.registries: List[FunctionRegistry] = []
self.persistence_manager = PersistenceManager(save_dir)
# ... a lot of code
```

### 2. 新增 `SimEngine.register_environment` 方法

**目的**: 提供一个清晰的公开接口，供用户手动注册他们自己的环境类。

**新增方法:**
```python
# inside the SimEngine class
def register_environment(self, name: str, env_class: Type[Environment]):
    """
    手动注册一个自定义的环境类。

    Args:
        name: 在配置文件中使用的名称 (例如, "my_custom_env")。
        env_class: 自定义环境的类对象。
    """
    if name in self.env_classes:
        logger.warning(f"正在覆盖已注册的环境: {name}")
    self.env_classes[name] = env_class
    logger.info(f"已注册新环境: '{name}'")
```

### 3. 修改 `SimEngine._create_initial_world_state` 方法

**目的**: 在创建世界时，使用我们新的环境注册表来实例化正确的环境对象。

**修改前:**
```python
# in SimEngine._create_initial_world_state
# ... (agent creation logic)

# Create environment
env_config = self.config.get('environment', {})
environment = Environment(
    type=env_config.get('type', 'base'),
    state=env_config.get('state', {})
)

# ... (world_state creation)
```

**修改后:**
```python
# in SimEngine._create_initial_world_state
# ... (agent creation logic)

# --- 使用注册系统创建环境 ---
env_config = self.config.get('environment', {})
env_type = env_config.get('type', 'base')
env_state = env_config.get('state', {})

EnvClass = self.env_classes.get(env_type)
if not EnvClass:
    logger.warning(f"环境类型 '{env_type}' 未找到。将使用基础环境。您是否忘记了注册它？")
    EnvClass = Environment

# 将特定于环境的配置传递给它的 state，以便它可以使用
env_state['config'] = env_config

environment = EnvClass(type=env_type, state=env_state)

# ... (world_state creation)
```

以上就是全部建议修改。请您审阅。