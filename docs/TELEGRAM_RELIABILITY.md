# Telegram update durability

MATRIX's Telegram bridge uses long polling and a durable update watermark.

## Delivery behavior

- The last successfully handled Telegram `update_id` is persisted in the SQLite `system_flags` table as `telegram_update_offset`.
- On restart, polling resumes from the next update ID. Startup no longer drains and discards the pending update queue.
- The watermark advances only after the update handler returns successfully. A failed update remains eligible for redelivery.
- Every task created from a Telegram update carries a unique `source_update_id` in SQLite. A replay after a crash returns the existing task instead of creating a duplicate task.
- Unauthorized chats and unsupported message shapes are ignored and acknowledged by advancing the offset, so they do not block later owner messages.

## Delivery guarantee

Telegram polling plus the SQLite watermark provides at-least-once update handling with idempotent task creation. Telegram `sendMessage` itself cannot provide exactly-once delivery: if MATRIX sends a response and crashes before saving the watermark, that response may be sent again when the update is replayed. Repeated `/stop` is safe and idempotent; public or external task actions still pass through MATRIX's normal approvals and recovery rules.

## Recovery

The task row is persisted before enqueue. If the process stops after task creation but before the update watermark is committed, the replay finds the same task by its source update ID. Normal task startup recovery remains responsible for safely requeuing untouched RECEIVED tasks and refusing uncertain in-flight side effects.
