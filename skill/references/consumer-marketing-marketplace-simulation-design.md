# Consumer, Marketing, And Marketplace Simulation Design

Use this guide for Society0 simulations about consumer choice, marketing interventions, price promotions, word-of-mouth, buyer/seller agentic markets, search, negotiation, transaction, marketplace design, welfare, manipulation, and consumer-facing agent behavior. Load it after `founder-experience.md`, `research-design.md`, `environment-design.md`, `agent-design.md`, and `step-dsl.md`; also load `simulation-paper-distillation.md` when adapting a paper.

Route macroeconomics, financial markets, banking, and household labor/consumption to the economics/finance guides first. Use this guide when the mechanism is consumer-facing choice, marketing exposure, or marketplace transaction design.

## Evidence Map

| Source | Evidence status | Society0 lesson |
| --- | --- | --- |
| Chu et al., *LLM-Based Multi-Agent System for Simulating and Analyzing Marketing and Consumer Behavior*, arXiv:2510.18155, https://arxiv.org/abs/2510.18155 and official GitHub repository | Supported by paper and official code/config README. | Marketing simulations need hosted location, budget, menu, energy/need constraints, visible discount timing, purchase records, social conversations, and safeguards against impossible purchases. |
| Bansal et al., *Magentic Marketplace: An Open-Source Environment for Studying Agentic Markets*, Microsoft Research PDF and https://github.com/microsoft/multi-agent-marketplace | Supported by paper and official code/config README. | Agentic marketplaces need an env-owned protocol for search, messaging, proposals, payments, catalog state, welfare metrics, manipulation tests, and search/consideration-set ablations. |

## Domain Stance

Model consumer and marketplace simulations as **transaction environments**:

```text
marketplace / town / catalog -> bounded product and social FoVs
-> search, conversation, purchase, negotiation, or payment action
-> hosted budget, inventory, transaction, and satisfaction updates
-> auditable consumer, seller, welfare, and fairness records
```

The environment owns locations, catalogs, prices, discounts, budgets, menus, search results, seller claims, transaction ledger, visibility of promotions, and satisfaction rules. LLM agents own situated interpretation: need recognition, preference articulation, trust, search strategy, negotiation, word-of-mouth, susceptibility to persuasion, and purchase reasoning.

## Target Taxonomy

- **Marketing promotion simulation**: a price discount, ad, coupon, loyalty message, or social campaign changes awareness and purchase behavior.
- **Consumer journey and word-of-mouth**: agents plan routines, visit locations, talk with peers, remember offers, and form habits or loyalty.
- **Two-sided agentic marketplace**: assistant agents represent consumers; service agents represent businesses; the env hosts search, communication, proposals, and payments.
- **Market design and safety**: search ranking, consideration set size, manipulation tactics, response-order bias, trust systems, and welfare/fairness effects are treatments.

## Society0 Construction Rules

Use this split:

| Component | Society0 location |
| --- | --- |
| Catalog, prices, discounts, inventory, budgets, locations, search index, transaction protocol, payment rules, satisfaction function | env state, env config, rules |
| Consumer preferences, need state, income, budget, loyalty, prior purchases, awareness, seller profile | agent state/properties |
| Search results, displayed prices, promotion visibility, peer comments, seller messages, prior transaction history | FoV |
| Search, ask seller, send offer, propose order, accept/reject, pay, buy, visit, recommend, share experience, complain | `instruct` with typed env actions |
| Satisfaction, purchase intent, perceived manipulation, trust, reason for choice | `interview` or analysis step |
| Welfare, fit, price, discount, market share, proposal order, response latency, manipulation arm | output tables |

## Paper-Derived Patterns

### Discounts Must Be Visible Conditions, Not Hidden Outcomes

Supported by paper and official code/config:
The marketing-town paper runs an 11-agent, 10-location, 7-day town with a 20% midweek discount, budgets, food/grocery need constraints, daily planning, conversations, and sales metrics. The README identifies config files for personas, menus, discount config, simulation constants, prompts, and transaction metrics.

Inference for Society0 mapping:
The discount should appear only in FoVs where a consumer could know it: menu display, advertisement, peer message, or prior visit. Record `discount_visible`, `discount_active`, and `discount_used` separately.

### Transaction Protocols Belong To The Environment

Supported by paper and official code/config:
Magentic Marketplace defines assistant and service agents, marketplace catalog/search, communication, order proposals, payments, transaction fulfillment, consumer welfare, fairness/efficiency metrics, manipulation experiments, and consideration-set ablations.

Society0 mapping:
Use typed actions such as:

```text
search(query, constraints)
message_seller(seller_id, content)
propose_order(seller_id, items, price)
accept_order(proposal_id)
pay(proposal_id)
decline(proposal_id, reason)
```

The env should validate seller IDs, catalog items, prices, budget, and whether a proposal satisfies the consumer request. Never rely on the agent's final prose as proof of transaction.

### Measure Search And Manipulation As Market Conditions

Supported by paper:
Magentic Marketplace compares lexical versus perfect search, varies consideration-set size, tests manipulation strategies, and finds proposal-order bias and scale-related performance degradation.

Society0 mapping:
Record search candidate rows with rank, score, shown status, and hidden fit. Record proposal order and response latency. Test manipulation arms with explicit labels hidden from agents and report welfare, satisfaction, seller selection, and unfair advantage.

## Dirty-Work Triage

Can do now:
- Build a small market pilot with 5-20 consumers, 3-10 sellers/products, one promotion or search treatment, and a transaction ledger.
- Add rule baselines for perfect search, random seller choice, cheapest satisfying seller, and no peer influence.

Need user input:
- Product category, target consumer population, acceptable marketing intervention, utility/satisfaction definition, and whether the scenario is real or synthetic.

Optional external pipeline:
- Product catalogs, prices, transaction logs, ad copy, review corpora, search indexes, and human benchmark choice tasks.

Society0 scaffold impact:
- Use `plain` for one-step purchase-intent studies. Use a custom env when search, proposals, budget, location, inventory, or payments are central.

## Validation And Boundaries

- Do not present simulated consumer behavior as demand forecasting without external validation.
- Report model/provider, prompt version, search algorithm, catalog construction, hidden fit function, and transaction validator.
- Include repeated seeds, rule baselines, search-quality ablations, manipulation tests, and subgroup error checks.
- For real commercial use, require human review, fairness assessment, consumer protection review, and privacy/data licensing checks.
