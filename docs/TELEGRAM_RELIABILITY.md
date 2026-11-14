# Telegram update durability

MATRIX's private Telegram bridge uses long polling with a durable SQLite update watermark.

## Delivery behavior

- The last successfully handled Telegram `update_id` is stored in `system_flags.telegram_update_offset`.
- On restart, polling resumes at the next update ID. Startup does not drain or discard pending owner messages.
- The watermark advances only after the update handler completes successfully. A failed update is retried on the next poll.
- Task creation uses MATRIX's durable idempotency table with scope `telegram.update` and the Telegram update ID as the key. Replayed messages return the original task rather than creating a second task.
- Existing idempotency is checked before the emergency-stop gate so replaying an already-created task does not create a new task or lose the original acknowledgement while the stop is active.
- Unauthorized chats and unsupported message shapes are ignored and acknowledged, so they do not block later owner messages.
- After normal orchestrator restart recovery, the Telegram bridge queries the durable task table for the bounded set of currently nonterminal tasks created through the Telegram idempotency scope, then reattaches result watchers. This is not limited to the most recent messages, so older still-active work is not hidden by newer completed tasks. Stale/missing records are audited and do not prevent polling from starting.
- Task notifications retry transient Telegram delivery failures a bounded number of times. The owner can also retrieve a recorded result later with `/task <task-id>`; `/tasks` shows the five most recent Telegram tasks.
- `/cancel <task-id>` is limited to Telegram-submitted task IDs. `/resume` clears the emergency-stop flag but does not revive tasks cancelled by `/stop`.

## Delivery guarantee

Telegram polling provides at-least-once update handling with idempotent task creation. Telegram `sendMessage` cannot guarantee exactly-once responses: if MATRIX sends a reply and crashes before persisting the update watermark, the reply may be sent again after restart. Repeated `/stop` is safe and idempotent. Public or external task actions still pass through MATRIX's normal policy, exact-action approval and verification rules.

## Recovery and acceptance

Task rows are persisted before enqueue. If MATRIX stops after task creation but before the update watermark is committed, replay finds the same task through the idempotency key. Normal startup recovery remains responsible for requeueing untouched RECEIVED tasks and refusing uncertain in-flight side effects.

Automated regression tests cover cursor restoration, success-only watermark persistence, failure retry behavior and duplicate task prevention even when the emergency stop is active. Real bot delivery, owner-chat restriction, process restart and response replay still require acceptance on the owner's configured Windows host.
