# Startup recovery

MATRIX reconciles persisted tasks during application startup, before the scheduler begins dispatching new work.

## Recovery rules

- **RECEIVED:** safe to enqueue because no plan step or tool side effect has started.
- **APPROVAL_REQUIRED / WAITING_FOR_USER with a live pending approval:** remain waiting. The exact approval remains visible to the owner and is not silently accepted.
- **APPROVAL_REQUIRED / WAITING_FOR_USER with an already-approved action:** resume through the normal policy and tool path. The executor recomputes the action fingerprint and requires the stored approval to match the exact tool name and validated arguments.
- **Expired approval:** mark it expired, fail the step, and stop without executing the action.
- **Any other nonterminal state:** mark running calls and steps failed, report an uncertain outcome, and do not replay the operation.

## Why interrupted operations are not retried

A process can stop after an external side effect has happened but before MATRIX persists the tool result. The database alone cannot prove whether that action took place. Automatically replaying a delete, move, message, purchase, or other non-idempotent operation could duplicate or compound the effect.

MATRIX therefore preserves verified completed-step evidence, stops the interrupted task as failed or partial, and writes an audit event. The owner should inspect the external state before creating a replacement task.

## Audit events

Recovery emits task-level events such as `task.recovery_requeued`, `task.recovery_waiting`, and `task.recovery_stopped`, followed by `system.recovery_completed` with aggregate counts.

## Scope

This is conservative crash recovery, not rollback or automatic exactly-once execution. Exactly-once behavior for external systems requires provider-supported idempotency keys or a read-after-write reconciliation adapter; MATRIX does not assume those guarantees.
