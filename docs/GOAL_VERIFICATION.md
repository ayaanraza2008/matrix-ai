# Goal criteria review and confirmation

MATRIX separates three different claims:

1. **Task-step verification** — each planned action returned acceptable tool-level evidence.
2. **Criteria review candidate** — a configured model compares the owner's success criteria with the persisted task summary and structured evidence. The result is advisory and may be `met`, `not_met`, or `unclear`.
3. **Owner confirmation** — the authenticated owner explicitly records the final outcome and a reason.

A model review never changes a goal to `verified`. The goal remains `awaiting_verification` until the owner calls the confirmation endpoint. A negative owner decision changes the state to `needs_attention`. Confirmation is only accepted for a goal whose underlying task completed successfully and which already has a saved criteria review.

## API

- `POST /api/goals/{goal_id}/evaluate` — run a model-assisted candidate review. Requires a configured model and a completed task.
- `POST /api/goals/{goal_id}/confirm` — owner-only final decision.

Example confirmation body:

```json
{
  "outcome": "met",
  "reason": "I checked the recorded evidence against the success criteria."
}
```

Possible criteria states include `not_evaluated`, `candidate_met`, `candidate_not_met`, `needs_owner_review`, `owner_confirmed_met`, and `owner_confirmed_not_met`.

The review is not a substitute for independent external evidence. The evaluator receives only stored task evidence and summary, not arbitrary tool access. If the model is unavailable or returns malformed JSON, evaluation fails closed and the goal is not marked verified.
