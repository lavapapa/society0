# Legacy YAML Schedule

The previous YAML workflow/studio scheduler is retained under `society0.legacy.schedule`.

New Society0 experiments should use the code-driven API:

```python
from society0 import Society0
```

Legacy imports:

```python
from society0.legacy.schedule import Schedule
```

This module is kept for old experiments and future studio work. New runtime code is not required to stay compatible with legacy selector/operator/converter workflows.
