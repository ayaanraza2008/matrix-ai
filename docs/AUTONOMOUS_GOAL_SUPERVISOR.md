# Autonomous goal supervisor — implementation contract

## Objective

Make MATRIX independently complete clearly scoped goals end-to-end: understand intent, gather context, plan, delegate, execute permitted actions, verify outcomes, recover safely, and report evidence. "100% autonomy" is measured against a declared capability catalogue and acceptance suite; it is not permission to bypass safety controls or claim success without evidence.

## Existing baseline (verified on the current stacked branch)

- The orchestrator persists tasks, plans up to 12 steps, enforces deadlines, checks cancellation, validates tool schemas, evaluates risk/scopes, verifies tool evidence, and safely retries only idempotent low/medium-risk calls.
- Planner uses deterministic heuristics for known intents and an optional configured model for general multi-step plans. With `model_provider=none` (the current default), general planning degrades to a fallback response.
- The Agency worker pool routes specialist profiles and runs bounded parallel workers, but it does not give workers independent tool authority.
- SQLite-backed memory and recurring jobs exist. The scheduler queues owner-created jobs subject to the global autonomy ceiling.
- Restart recovery resumes untouched tasks and valid exact approvals, but does not replay uncertain in-flight side effects.
- Autonomy defaults to L2. HIGH and CRITICAL actions require exact, time-limited owner approval; emergency stop, scopes, deadlines and verification remain enforced.

## Required supervisor lifecycle

1. **Intake:** convert a user goal into a typed goal record with owner, purpose, workspace, allowed capabilities, prohibited actions, deadline, budget, recurrence and success criteria. Ask only for missing information that changes risk or makes execution impossible.
2. **Context:** retrieve relevant memory and workspace state; label all retrieved content as untrusted data, never authority.
3. **Plan:** produce a dependency graph with explicit preconditions, expected outputs, risk classification, idempotency and verification for every action. Reject plans that exceed limits or require unavailable tools.
4. **Delegate:** select specialists by capability and confidence; run them in bounded parallelism; treat reports as fallible proposals. The supervisor alone owns execution and policy checks.
5. **Execute:** schedule independent safe steps concurrently where dependencies permit; serialize conflicting writes; checkpoint before and after side effects; enforce per-goal time, cost, token and tool-call budgets.
6. **Observe:** persist progress, tool outputs, verification evidence, retries, approvals, failures and cancellation. Support pause, resume and owner-visible progress.
7. **Verify:** verify each outcome using tool-produced evidence or an independent read-back. A model's statement is never proof of completion.
8. **Replan:** after a verified failure or changed precondition, re-read state and generate a bounded replacement plan. Never repeat an uncertain non-idempotent action; reconcile its external effect first.
9. **Recover:** retry only idempotent actions within configured limits; use exponential backoff with jitter for transient errors; isolate repeated failures and circuit-break unhealthy integrations.
10. **Finish:** report completed, partial, blocked or failed with evidence, unresolved risks and next safe action. Never silently mark partial work complete.

## Autonomy and consent policy

| Class | Examples | MATRIX behavior |
|---|---|---|
| Read-only | Search approved workspace, inspect status, summarize | May run autonomously within granted scopes |
| Reversible low-impact | Create draft, organize a copy, prepare a local report | May run autonomously within explicit workspace and resource limits |
| External or consequential | Send messages, publish content, modify shared records, change device settings | Require a task-specific owner policy; default to preview/approval |
| High-impact / irreversible | Delete originals, spend money, expose secrets, change security/access, execute arbitrary code | Require exact, time-limited owner approval; never self-approve |
| Emergency stop | Stop current and queued work | Always available to owner; cannot be overridden by a model or agent |

MATRIX must not grant itself scopes, change its own autonomy ceiling, approve its own requests, manufacture credentials, disable audit logs, or reinterpret untrusted content as owner consent.

## Production work packages and exit gates

### A. Goal lifecycle and supervisor
- Add persistent goal records, task-to-goal linkage, explicit state transitions, pause/resume/cancel, recurrence, budgets and retention.
- Add a supervisor loop with bounded planning/replanning and concurrency-aware execution.
- Tests: duplicate submission, dependency ordering, crash/restart, pause/resume, cancellation, deadline, budget exhaustion, concurrent writes and idempotency.

### B. Model and decision quality
- Make local model setup discoverable and benchmarked; expose configured/degraded status prominently.
- Add structured plan validation, uncertainty thresholds, evaluator checks and deterministic fallback behavior.
- Tests: malformed model output, prompt injection, conflicting specialist reports, unavailable model, low-confidence plan and model timeout.
- A configured model is a prerequisite for general autonomous reasoning; never imply autonomy while the planner is in fallback mode.

### C. Tools and integrations
- Turn credential probes into complete least-privilege provider workflows with OAuth lifecycle, revocation, rate limits, retries, audit and end-to-end tests.
- Add supported browser/desktop workflows with explicit per-app permissions, user-visible actions and recovery.
- Keep external publication, purchases, account/security changes and destructive operations approval-gated by default.

### D. Memory and learning
- Add explicit provenance, confidence, decay, user correction, retention and deletion semantics.
- Learn preferences only from approved evidence; never train on secrets or treat prior tool output as authorization.
- Tests: poisoned memory, conflicting memories, deletion, expiry, export and recovery.

### E. Reliability and security
- Close SSRF DNS-rebinding/TOCTOU gaps, protect browser tokens from script-readable storage, use OS-backed secret storage, encrypt sensitive local state where feasible, and add signed update/release provenance.
- Add circuit breakers, queue backpressure, health alerts, structured metrics and safe shutdown.
- Tests: network interruption, disk-full, corrupted database, credential revocation, replay, rate limiting and Windows login/restart.

### F. Acceptance and release
- Publish a capability matrix with status: implemented, CI-tested, integration-tested, device-tested, production-accepted.
- Run end-to-end scenarios on Windows and supported Android devices, including real model inference, microphone/speaker, Telegram, private remote access and recovery.
- Ship signed installers/APKs, rollback and upgrade tests, SBOM, provenance and release notes.
- No phase is complete merely because code or a passing unit test exists.

## Completion score

Track two separate percentages:
- **Capability completion:** accepted capabilities / all explicitly declared target capabilities.
- **Autonomous task success:** independently completed, verified end-to-end test goals / all eligible end-to-end test goals.

Publish denominators, test set, hardware, model, integrations and date with every score. Exclude blocked tasks only when the blocker is explicitly outside MATRIX's declared scope. Do not use a subjective "100%" badge.

## Immediate priority order

1. Extend the initial durable goal/task vertical slice into a multi-task supervisor with bounded replan/recovery.
2. Make local model setup and degraded-mode visibility reliable.
3. Add end-to-end capability adapters and safe desktop/browser execution.
4. Close critical security gaps and add failure-injection tests.
5. Validate real Windows and Android operation, then package signed releases.


## Initial implementation status (PR #22)

The first implementation slice now adds:

- SQLite schema v2 with durable goal records and a backward-compatible `tasks.goal_id` migration.
- Owner-scoped `POST /api/goals`, `GET /api/goals`, and `GET /api/goals/{goal_id}` endpoints.
- A goal-to-task link that permits only one attached task in this initial slice; duplicate attachment is rejected.
- Goal state synchronization from the orchestrator's terminal task transition: completed, partial, failed or cancelled.
- An owner-authorized goal cancellation endpoint that delegates to the existing task cancellation path.
- Audit events and dashboard table counts for goals, plus regression tests for task linkage, owner scoping and terminal-state synchronization.

This is a durable goal wrapper around the existing single-task orchestrator, **not yet the complete multi-task supervisor**. Success criteria are persisted as goal metadata but are not yet evaluated by a separate goal-level verifier. Pause/resume, dependency graphs spanning multiple tasks, bounded replanning, goal budgets and crash-recovery orchestration remain open work. The PR must stay draft until those capabilities and their tests are implemented and CI passes.


### Goal deadline and evidence metadata

Schema v3 adds a persisted goal deadline, task-step verification status, aggregate evidence and an explicit success-criteria status. Goal deadlines are bounded by the configured task deadline, so an API caller cannot silently extend the runtime's safety budget. A completed task maps to task_steps_verified only when the orchestrator's aggregate evidence says every step passed verification. The API deliberately reports criteria_status=not_evaluated: semantic evaluation of the user's free-form success criteria is a separate remaining capability, not something inferred from task completion.


### Honest goal completion semantics

A task reaching COMPLETE now moves its parent goal to awaiting_verification, not completed. The task's individual steps may all have strong execution evidence, but MATRIX does not yet independently evaluate arbitrary natural-language goal success criteria. The goal therefore stays explicitly pending semantic verification instead of claiming that task completion proves the whole goal was achieved.


### Bounded dependency-graph validation (infrastructure only)

The branch now includes a side-effect-free `GoalGraph` validator. It rejects empty/duplicate node IDs, unknown/self/duplicate dependencies, malformed dependency sequences, cycles, excessive graph size and excessive fan-in. It provides deterministic topological ordering and a bounded ready-node selector that does not unlock descendants of failed nodes.

This is a tested planning primitive, **not a persistent multi-task scheduler**: goal tasks are still single-linked in the current API, graph state is not yet stored in SQLite, and the orchestrator does not yet dispatch a goal DAG. Do not count multi-task execution, pause/resume, replanning or goal-level recovery as implemented until those are integrated and accepted end-to-end.


### Persistent goal DAG planning slice

The next implementation slice adds schema v4 persistence for bounded goal graph nodes and owner-scoped plan endpoints:

- `POST /api/goals/plan` validates a bounded acyclic dependency graph and stores its nodes in SQLite.
- `GET /api/goals/{goal_id}/graph` returns the persisted plan for its owner.
- Invalid cycles, unknown dependencies, duplicate IDs, and configured graph limits are rejected before a goal is written.
- Plans are explicitly marked `execution_status=plan_only`. This endpoint does **not** dispatch tasks or claim autonomous multi-task execution. The persistent scheduler, node-to-task dispatch, pause/resume, bounded replanning and goal-level verification remain follow-up work.


### Durable DAG execution slice

Schema v5 links each goal-node task to a unique `(goal_id, goal_node_id)` identity. The owner can explicitly start a persisted plan with `POST /api/goals/{goal_id}/start`. MATRIX dispatches at most four dependency-ready nodes, creates normal orchestrator tasks for them, and only unlocks dependants after a node task reaches terminal completion with verified step evidence. Failed or cancelled prerequisites block descendants. Node/task linkage is reconciled during startup recovery, and completed nodes trigger the next ready wave automatically.

This is bounded node-level execution, not yet full goal autonomy: there is no semantic success-criteria verifier, pause/resume endpoint, retry/replanning budget, or cross-process lease protocol. In-flight tool effects retain the orchestrator's conservative manual-review recovery behavior.

Owner cancellation of a started DAG first changes the goal to a non-dispatchable `cancelling` state, marks pending nodes cancelled, then cancels linked running tasks and records the final goal as cancelled. This prevents task-finalization callbacks from launching another wave during cancellation.


### Owner-controlled pause and resume

The DAG API now exposes owner-scoped POST /api/goals/{goal_id}/pause and POST /api/goals/{goal_id}/resume operations. Pausing is a durable dispatch gate: no additional goal nodes can be claimed or dispatched while the goal is paused. Tasks already in flight are **not** forcibly interrupted; they may finish their current bounded work, but completion cannot launch the next dependency wave until the owner resumes the goal. This avoids pretending that an external side effect can be safely suspended mid-request.

Resume is accepted only from the persisted paused state and immediately attempts to dispatch dependency-ready nodes through the ordinary orchestrator. Existing task approvals, scopes, cancellation, deadlines and evidence checks remain unchanged. A process restart preserves the paused state; startup recovery schedules only running goals, so paused plans remain paused until an owner explicitly resumes them.
