# Multi-agent system: architecture
Goal: the user gives a task. A central orchestrator splits it into ordered steps and assigns each step to a specific agent. Agents run one after another. Every output goes back to the orchestrator, which passes on only what the next agent needs. At the end a reviewer checks the final result against a checklist; if it fails, the faulty agent revises its work and the review repeats, up to 3 rounds.

Agent roles (must match the keys of ROLES in llm.py): orchestrator, reviewer, researcher, writer, tester.

Flow:
1. make_plan(task) -> Plan JSON: steps (id, agent, instruction, depends_on) plus a checklist of 3-6 concrete, checkable criteria written BEFORE any work starts. The last step produces the final deliverable.
2. The executor runs steps in order. Each agent receives its instruction plus ONLY the outputs of the steps in depends_on. State is saved to runs/<run_id>/state.json after every step.
3. review(...) -> Verdict: passed, plus issues (step_id, problem, fix).
4. On fail: re-run each faulty step with its previous output and the reviewer's problem/fix attached, then re-run every step that depends on it, then review again. Stop after 3 rounds and return the best attempt with the open issues listed.

Rules:
- Agents never call each other; everything goes through the orchestrator.
- All model calls go through an injectable function llm(role, system, user, json_mode=False) that defaults to call_llm, so everything can be tested with a fake.
- No API keys in code; keys come from environment variables (.env for local use, never committed).
- Plain Python control loop, no agent framework. Pydantic v2 for all JSON schemas. Use pathlib, utf-8, and nothing bash-only (Windows/PowerShell).
- Every stage ships with pytest tests that use a fake llm (no network).
