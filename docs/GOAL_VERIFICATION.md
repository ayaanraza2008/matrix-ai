# Autonomous goal graphs and criteria verification

MATRIX distinguishes planned task completion from confirmed goal success.

## Goal criteria lifecycle

1. **Task-step verification** — each planned action returns acceptable tool-level evidence.
2. **Criteria review candidate** — when a model is configured, MATRIX compares the owner's criteria with the persisted task summary and structured evidence. The result is advisory: `met`, `not_met`, or `unclear`.
3. **Owner confirmation** — only the authenticated owner can record the final outcome and a reason. Without a configured model, the owner may confirm directly after inspecting the persisted task evidence.

A model review never changes a goal to `verified`. The goal remains `awaiting_verification` until the owner confirms. A negative decision changes it to `needs_attention`.

## Multi-task goal DAGs

- `POST /api/goal-graphs` creates a persistent directed acyclic graph of goal nodes.
- `GET /api/goal-graphs` and `GET /api/goal-graphs/{graph_id}` return owner-scoped graph state.
- `POST /api/goal-graphs/{graph_id}/cancel` cancels running node tasks and prevents queued nodes from dispatching.
- Each node is an ordinary MATRIX goal with its own task, deadline, evidence, audit trail and owner confirmation.
- Root nodes are dispatched first, up to a bounded concurrency limit. A dependent node is dispatched only after all prerequisites are owner-confirmed as `verified`.
- A failed prerequisite blocks its transitive descendants; independent branches may continue. An emergency stop prevents new nodes from being dispatched.
- The supervisor persists graph state and re-reads it on every tick, so the graph can resume scheduling after a process restart once ordinary task recovery has reconciled in-flight tasks.

Example request:

```json
{
  "title": "Prepare and publish a campaign",
  "deadline_seconds": 3600,
  "nodes": [
    {
      "node_id": "prepare",
      "title": "Prepare the campaign",
      "success_criteria": "A reviewed campaign draft exists.",
      "depends_on": []
    },
    {
      "node_id": "publish",
      "title": "Publish the campaign",
      "success_criteria": "The campaign is live and its URL is recorded.",
      "depends_on": ["prepare"]
    }
  ]
}
```

The model has no ability to mark prerequisites verified or grant itself permissions. Owner confirmation is the gate between graph stages.

## Criteria API

- `POST /api/goals/{goal_id}/evaluate` runs/re-runs a model-assisted candidate review. It requires a configured model and a successfully completed task.
- `POST /api/goals/{goal_id}/confirm` records the owner's explicit decision.

Example confirmation:

```json
{
  "outcome": "met",
  "reason": "I checked the recorded evidence against the success criteria."
}
```

Possible criteria states include `not_evaluated`, `candidate_met`, `candidate_not_met`, `needs_owner_review`, `owner_confirmed_met`, and `owner_confirmed_not_met`.

The evaluator receives only saved criteria, task summary and persisted evidence. It has no tool access. If the model is unavailable or returns malformed JSON, the review fails closed; a task is not downgraded and a goal is not silently verified.
