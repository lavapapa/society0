# Researcher Onboarding

Use this guide when someone has just installed the Society0 skill, is new to Society0, is unsure where to begin, or asks to understand the project. The skill has no reliable installation-complete event, so treat onboarding as an intent-based conversation: offer it on first use or when useful, while letting users with a specific request go directly to that task.

The Society0 skill guides a coding agent. The Society0 Python package is the simulation engine used by an experiment. Explain this distinction before setup becomes relevant. LLM-Agent experiments need both an LLM provider and an embedding provider; rule-based baselines can be useful when they faithfully express a deterministic mechanism. Do not ask users to paste provider credentials into chat; explain how to configure them locally when the selected path needs them.

## First response

Welcome the researcher in the language they used, explain in one or two plain-language sentences that Society0 helps researchers study how LLM Agents behave inside a designed social setting, and state that simulated behavior can help explore mechanisms and compare assumptions but is not direct evidence about real people. Then offer a small numbered menu. Let the user choose a number, describe another goal, or skip the menu and state their request.

1. **Start a study:** “I have a social-science question and want to design my first LLM-Agent social simulation.”
2. **Learn Society0:** “Teach me how Society0 works, one idea at a time.”
3. **Adapt a paper:** “Help me understand a paper and adapt its simulation design.”
4. **Explore existing work:** “Help me understand or analyze an existing Society0 project, run, or output.”

For a Chinese-speaking researcher, a suitable opening is:

> Society0 帮助研究者模拟由 LLM Agent 参与的社会情境，观察这些 Agent 在给定信息和互动规则下会怎样行动。模拟结果适合探索机制、比较假设，不能直接代表现实人群。你想从哪条路开始？
>
> 1. 我有一个研究问题，想设计第一项 LLM Agent 社会模拟。
> 2. 我想循序渐进地理解 Society0 的原理。
> 3. 我想从一篇论文开始，讨论如何复现或改造。
> 4. 我已有项目或运行结果，想弄清楚代码、数据或问题。

## Follow the chosen path

For a new study, begin with the phenomenon or puzzle in the researcher's own words. Accept an unfinished idea, a short description, or an optional paper or research artifact. Ask one high-value question at a time; gradually clarify the intended outcome, who participates, what they can see and do, how conditions differ, and what evidence the researcher wants to inspect. Do not make the researcher supply framework terminology. Periodically restate the emerging design in ordinary language and invite corrections.

Before implementation, summarize a small first study in plain language: the social setting, what participants can perceive and do, the conditions being compared, the observations to collect, and what the first analysis could and could not establish. Map this design to Society0's environment, LLM Agents, interactions, and measurements only as those concepts become useful. Explain provider and package setup when the selected study needs it, not as a prerequisite to discussing the research question.

For a paper, first explain its research question, mechanism, agents, setting, interventions, and measurements in accessible language. Separate what the paper states from what the researcher proposes to change, then discuss which elements can be represented in Society0. For an existing project or run, orient the user with the relevant code and artifacts and proceed directly to their question.

## Teach Society0 step by step

When the user wants to understand the project, teach one concept per response and connect each concept to a concrete social-science example. Use this progression, adapting to the questions the researcher asks:

1. **Purpose and boundary:** what an LLM-Agent social simulation can help explore, and why its outputs do not by themselves establish facts about real populations.
2. **LLM Agents:** how an agent receives instructions and information, generates a decision, and may use available actions; explain that its behavior depends on the model, prompt, and information it receives.
3. **The social setting:** why Society0 designs the environment first; introduce what participants can see (FoVs) and do (actions) only after explaining those ideas in ordinary language.
4. **The study over time:** how participants interact across steps, how researchers vary conditions, and where deterministic rules or measurements fit alongside LLM Agents.
5. **Evidence and interpretation:** how metrics and qualitative traces describe the simulation, and how comparisons, repeated runs, baselines, and sensitivity checks support stronger interpretation.

After every teaching response in this path, add a short section headed **“你可能还想问：”** in Chinese, or a natural equivalent in the user's language, with at least three distinct, specific follow-up questions. Tailor them to the concept just explained, make them easy to choose by number, and allow the researcher to ask something else. For example, after explaining the environment:

1. “为什么要先设计环境，再设计 LLM Agent？”
2. “LLM Agent 能看到的信息具体由谁决定？”
3. “环境里的动作会怎样影响后续参与者？”

Do not turn this into a quiz unless the researcher asks for one. Keep the explanations cumulative: briefly connect the new idea to what has already been covered, and avoid repeating the full introduction on each turn.

## Before the formal experiment

Once the study design and approximate scale are clear, ask whether the researcher wants a token and model-cost estimate before the formal run. Make this optional and easy to answer, for example: “正式实验开始前，你想先估算一下 token 用量和模型费用吗？可以估算、先继续，或等实验规模确定后再算。” If the researcher declines or is undecided, continue the research workflow without treating that choice as a blocker.

If they want an estimate, ask for the model/provider and its current rates in the provider's billing units and currency. A useful template is:

```text
LLM model/provider: …
Input: … per 1 million tokens
Output: … per 1 million tokens
Cached input or reasoning-token rates, if billed separately: …
Embedding model/rate, if billed separately: …
Currency and billing unit: …
```

For example, “input $2.00 / 1M tokens; output $8.00 / 1M tokens” shows the format only; it is not a current quote for any model. Do not guess current tariffs. If the user supplies a pricing page or a rate card, use the rates and conditions they provide; distinguish cached-input, reasoning-token, regional, batch, or embedding rates when they change the calculation.

Estimate each interaction on each step: requests ≈ Σ(number of LLM Agents selected for that interaction on the step × times it runs per selected agent × expected model turns). Sum across steps and interactions, then add separate LLM work such as memory extraction, structured-output repair, surveys, or retries when applicable. A previous representative pilot is the best basis for input-token and embedding estimates. When no pilot exists, estimate from the planned context and state the assumptions; if prompt length cannot be converted reliably to tokens, give low, typical, and conservative scenarios instead of false precision.

For a rough output ceiling, multiply the number of applicable requests by the per-response `max_tokens` cap. This cap applies to each response, not the whole experiment. `max_turns` bounds an agent interaction loop when set to a finite value; make that assumption explicit. Input tokens vary with instructions, visible environment state, available actions, and retrieved memory. Embeddings, retries, structured-output repair, and other model operations can add cost. Concurrency primarily affects how requests overlap and the time to finish; it does not by itself reduce token charges.

Calculate the language-model portion using the supplied rates, for example:

```text
estimated input cost  = input tokens  × input rate  ÷ 1,000,000
estimated output cost = output tokens × output rate ÷ 1,000,000
estimated total       = input cost + output cost + separately priced resources
```

A clearly hypothetical example: suppose a plan has 1,000 requests, about 2,000 input tokens per request, and an expected 100 output tokens per request, with `max_tokens=200`. At researcher-supplied rates of $2 / 1M input tokens and $8 / 1M output tokens, the estimated language-model cost is $4.00 input + $0.80 output = $4.80. If every response reaches its 200-token cap while input use stays at the same estimate, the output-at-cap scenario is $4.00 + $1.60 = $5.60. These scenarios exclude separately priced embeddings, retries, and other calls; state those assumptions and any unknowns with the estimate. Describe the result as a planning amount; the current run process does not enforce it as a spending ceiling.

If the conservative scenario exceeds the researcher's stated target, show which assumptions or study-scale choices drive the estimate and offer scientifically acceptable alternatives for the researcher to choose. Do not silently reduce agents, steps, memory, action loops, or measurement fidelity to reach a price target.

After a run, use `summary.json -> resources.llm.prompt_tokens` and `completion_tokens` when the provider reports them, and use `resource_calls.jsonl` for call-level attribution. Society0's standard summary may report embedding call and text counts without a provider-independent embedding-token total; calculate that charge only when usage or a reliable tokenizer is available, otherwise mark it unknown. Apply the supplied rates to observed usage and clearly separate measured charges from projected future runs. See [run-monitor-analyze.md](run-monitor-analyze.md) for resource fields and output interpretation.
