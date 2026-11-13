# Persistent goal DAG execution

MATRIX goals can be represented as bounded directed acyclic graphs (DAGs). The owner creates a plan, reviews it, and starts it explicitly:

- `POST /api/goals/plan` validates and persists a plan (1–64 nodes, at most 16 dependencies per node).
- `POST /api/goals/{goal_id}/start` starts bounded dispatch (up to four ready nodes concurrently per goal).
- `GET /api/goals/{goal_id}/graph` returns persisted node state and task links.
- `POST /api/goals/{goal_id}/pause` stops new dispatch while already-running nodes finish.
- `POST /api/goals/{goal_id}/resume` explicitly reopens dispatch for an owner-paused goal.
- `POST /api/goals/{goal_id}/cancel` cancels the goal and any active node task.

Every node is a normal MATRIX task. The GoalSupervisor does not call tools directly: it creates a `goal_node` task and sends it through the existing planner, policy engine, scope checks, approval workflow, orchestrator, task-level retries, verifier and audit trail. At most four ready nodes are dispatched concurrently per goal. A node is eligible only after every dependency is completed.

## Failure and recovery

- A failed node blocks its transitive descendants.
- Independent branches remain eligible and may complete.
- An uncertain or terminal node is never blindly replayed.
- A crash before task creation resets an orphaned dispatch claim; a persisted task link is reconciled against the task's durable state.
- On startup, MATRIX reconciles ordinary tasks first, then resumes planned/running goal graphs. Explicitly paused goals stay paused until the owner resumes them.
- A node paused for owner approval remains pending in the graph; approving or denying its task resumes graph reconciliation.
- Goal deadlines are enforced before each dispatch.

## Goal-level verification

Task-level completion is not automatically equivalent to meeting the owner's goal criteria. If a local model is configured, MATRIX performs a separate, read-only criteria-assessment pass over task summaries and verification checks. The result is recorded as `verified_by_model`, `not_verified`, or `insufficient_evidence`. This is a model assessment, not external proof. When no model is configured or the assessment is invalid, the goal remains `awaiting_verification` with `pending_owner_review`; MATRIX does not silently mark it complete.

The assessment does not execute tools, approve actions, or override tool evidence. Untrusted task content is supplied as evidence only. For public posts, messages, purchases, deletions, calls, or other high-impact actions, the existing per-action approval requirements remain in force.

## Current limits

- Dispatch is deliberately bounded (up to four active nodes per goal); dependencies still gate each node.
- Goal-level automatic replanning is not enabled. Tool-level retries remain bounded by the orchestrator and its idempotency/risk rules. This avoids replaying side effects when an external provider's outcome is uncertain.
- The model-based criteria pass is not an external auditor. Goals without sufficient evidence require owner review.
- Live integration credentials, provider quotas, local model availability, Windows audio hardware and Android devices are environment-dependent and must be acceptance-tested separately.
