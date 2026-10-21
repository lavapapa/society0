# 工具调用索引

保留操作顺序及调用对象；完整参数与回执在本机原始记录中。文件正文见同目录快照。

## seq 19 · skill

```json
{
  "name": "society0"
}
```

## seq 24 · skill

```json
{
  "name": "fixture-actor-materials"
}
```

## seq 29 · read

```json
{
  "file_path": "$SKILL_LIBRARY/agent/gpt-with-taste/references/main.md"
}
```

## seq 30 · read

```json
{
  "file_path": "$SKILL_LIBRARY/INDEX.md"
}
```

## seq 33 · working_directory

```json
{}
```

## seq 35 · read

```json
{
  "file_path": "$TEST_ROOT/actors/.dsh/skills/society0/references/founder-experience.md"
}
```

## seq 36 · read

```json
{
  "file_path": "$TEST_ROOT/actors/.dsh/skills/society0/references/design-first-pass.md"
}
```

## seq 37 · read

```json
{
  "file_path": "$TEST_ROOT/actors/.dsh/skills/society0/references/actor-scope.md"
}
```

## seq 44 · read

```json
{
  "file_path": "$SKILL_LIBRARY/agent/gpt-with-taste/references/research.md"
}
```

## seq 45 · read

```json
{
  "file_path": "$SKILL_LIBRARY/agent/gpt-with-taste/references/writing-docs.md"
}
```

## seq 46 · read

```json
{
  "file_path": "$TEST_ROOT/installed/society0/references/design-revision.md"
}
```

## seq 47 · read

```json
{
  "file_path": "$TEST_ROOT/installed/society0/references/economics-finance-simulation-design.md"
}
```

## seq 48 · read

```json
{
  "file_path": "$TEST_ROOT/installed/society0/references/economics-finance-macro-urban-simulation.md"
}
```

## seq 49 · read

```json
{
  "file_path": "$SKILL_LIBRARY/ui/society0-workbench/SKILL.md"
}
```

## seq 59 · read

```json
{
  "file_path": "$SKILL_LIBRARY/ui/society0-workbench/references/quickstart.md"
}
```

## seq 64 · glob

```json
{
  "pattern": "**/*"
}
```

## seq 66 · glob

```json
{
  "pattern": "workbench/**"
}
```

## seq 68 · glob

```json
{
  "pattern": "**/*design*"
}
```

## seq 70 · glob

```json
{
  "pattern": "**/*.md"
}
```

## seq 75 · bash

```json
{
  "command": "python3 $SKILL_LIBRARY/ui/society0-workbench/scripts/create_workbench.py --output-dir workbench --title \"储能补贴退出与供应链调整\"",
  "workdir": "$TEST_ROOT/actors"
}
```

## seq 82 · read

```json
{
  "file_path": "$TEST_ROOT/actors/workbench/data.json"
}
```

## seq 83 · read

```json
{
  "file_path": "$TEST_ROOT/actors/workbench/project.json"
}
```

## seq 84 · read

```json
{
  "file_path": "$SKILL_LIBRARY/ui/society0-workbench/references/empty-states.md"
}
```

## seq 91 · read

```json
{
  "file_path": "$SKILL_LIBRARY/ui/society0-workbench/references/data-contract.md"
}
```

## seq 92 · read

```json
{
  "file_path": "$SKILL_LIBRARY/ui/society0-workbench/references/import.md"
}
```

## seq 93 · read

```json
{
  "file_path": "$SKILL_LIBRARY/ui/society0-workbench/references/acceptance.md"
}
```

## seq 100 · edit

```json
{
  "file_path": "$TEST_ROOT/actors/workbench/data.json"
}
```

## seq 105 · bash

```json
{
  "command": "python3 $SKILL_LIBRARY/ui/society0-workbench/scripts/render_workbench.py --project workbench",
  "workdir": "$TEST_ROOT/actors"
}
```

## seq 112 · sidebar_open

```json
{
  "target": "$TEST_ROOT/actors/workbench/index.html"
}
```

## seq 117 · ask_user_question

```json
{}
```
