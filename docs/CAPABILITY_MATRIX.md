# Capability Matrix

Status key: **done** = implemented, integrated and test-backed · **partial** = implemented with a documented
gap · **scaffold** = structure only, not runnable here · **blocked** = waiting on environment/hardware/credentials.

| Capability | Blueprint phase | Status | Evidence | Gap | Next step |
|---|---|---|---|---|---|
| Central orchestrator + explicit state machine | 1 | done | `core/orchestrator.py`, `core/state_machine.py`, `test_startup_recovery.py` | Uncertain in-flight side effects are stopped for manual review, never replayed | Add durable step-level recovery only where idempotency can be proven |
| Server-side policy engine (L0–L4, risk classes) | 1 | done | `core/policy.py`, `test_policy_engine.py` | Per-tool dynamic scopes beyond static list | Add scope delegation with expiry |
| Exact approval binding + TTL | 1 | done | `approvals` table, `test_a05_destructive_approval.py` | No push approval to Android yet | Wire FCM/WebSocket approval push |
| Typed tool registry + schema validation | 1 | done | `tools/`, `test_a03_tool_schema.py` | 14 tools; no UI-automation adapter | Phase 5: pywinauto/Playwright |
| Audit trail with redaction | 1 | done | `storage/store.py`, `security/redaction.py` | No rotation/retention policy | Add audit retention + export |
| Emergency stop | 1 | done | `test_a06_cancellation.py` | — | — |
| SQLite persistence, WAL, backup/restore | 1 | done | `storage/db.py`, `test_a23_backup_restore.py` | No scheduled backups | Scheduler job for daily backup |
| Local model provider abstraction | 2 | done | `models/base.py`, `test_a02_model_offline.py` | Only Ollama + OpenAI-compatible | Add llama.cpp direct binding if needed |
| Local model benchmark (English/Hindi plans, schema validation, latency and RSS sampling) | 2 | done | models/benchmark.py, test_model_benchmark.py | Real results depend on the owner's local model and PC | Run matrix benchmark-model on target hardware |
| Hardware-aware model manager | 2 | done | `models/manager.py`, `test_a24_resource_budget.py` | Profiles are declared, not auto-benchmarked | Benchmark harness (Phase 2 exit) |
| Tool-call JSON parsing/validation | 2 | done | `models/planner.py`, `test_planner.py` | No constrained grammar decoding | Evaluate GBNF/ollama grammar support |
| Local chat + deterministic intent routing | 2 | partial | `HeuristicPlanner`, `PlannerService` | No model configured by default; no embeddings | Benchmark Qwen 0.5B/1.5B on the 8 GB PC |
| Speech-to-text | 3 | partial | `voice/engines.py`, `test_voice_engines.py`, `test_a11_voice.py` | Optional model must download/cache and be exercised on target mic | Verify Hindi/English accuracy and CPU latency on target PC |
| Text-to-speech | 3 | partial | `voice/engines.py`, `test_voice_engines.py` | Depends on OS-installed speech voices and platform driver | Verify actual output and barge-in on target Windows device |
| Wake word / VAD | 3 | not started | — | Depends on STT stack | openWakeWord + Silero VAD |
| Voice session control: mute, barge-in, PTT state | 3 | done (control) | `test_a11_voice.py` | Audio backend | Integrate with chosen engines |
| Camera/screenshot capture sessions | 4 | done (control) | `vision/manager.py`, `test_a13_vision.py` | No frame source/MediaPipe | OpenCV/MediaPipe adapter |
| OCR | 4 | partial | `tools/vision_tools.py`, `test_a15_ocr.py` (skipped) | Tesseract absent on build machine | Install Tesseract; verify English/Hindi |
| Gesture detection | 4 | not started | — | MediaPipe not added | Evaluate CPU cost on Ryzen 5700U |
| Scoped filesystem tools | 5 | done | `tools/file_tools.py`, `test_a17_path_traversal.py` | No recursive delete by design | — |
| Allowlisted apps + commands | 5 | done | `tools/app_tools.py`, `tools/shell_tools.py`, `test_a16_command_allowlist.py` | Windows UI automation missing | pywinauto window/control tools |
| System telemetry | 5 | done | `tools/system_tools.py`, `test_a24_resource_budget.py` | No historical metrics | Add sampling job + chart |
| Browser automation | 5 | not started | — | Playwright absent | Phase 5 with site-policy guardrails |
| Governed memory + FTS5 | 7 | done | `memory/service.py`, `test_a09_memory.py`, `test_a10_memory_deletion.py` | No vector index | Optional embeddings only if FTS proves insufficient |
| Scheduler / recurring jobs | 7 | partial | `scheduler.py`, `/api/jobs`, A25 | No quiet hours/budgets | Add per-job limits + notifications |
| Proactive monitoring | 7 | partial | system tool + scheduler | No alert routing | Telegram/FCM when configured |
| GitHub integration | 8 | partial | `integrations/adapters.py` | Token not configured | Fine-grained token + repo read tools |
| Google Workspace | 8 | blocked | adapter reports `missing_credentials` | OAuth client absent | Owner consent flow |
| Telegram notifications | 8 | blocked | adapter + health check | Bot token absent | Configure bot + allowed chat ID |
| Home Assistant | 8 | blocked | adapter + health check | URL/token absent | Local network setup |
| Telephony (Twilio) | 8 | blocked | adapter, cost warning | Paid provider | Optional; never advertised as free |
| Firebase push | 8 | scaffold | adapter | Firebase project absent | Android FCM integration |
| JARVIS dashboard | 9 | done | `src/matrix_ai/web/`, `test_web_ui.py` | No desktop packaging | Phase 9 installer |
| Android companion | 6 | scaffold | `android/` | No Android SDK on build machine | Build APK, implement pairing/chat/camera |
| Device pairing + revocation | 6 | done | `security/auth.py`, `test_a19_device_pairing.py` | No QR-code pairing UI | Add QR generation |
| Remote access | 10 | documented | `docs/SETUP.md` | Tailscale not configured | Owner network setup |
| Secret scanning + CI | 9 | done | `.github/workflows/ci.yml`, `security/secret_scan.py` | No signed releases | Add pip-audit + build artifacts |
| Packaging / installer | 9 | not started | — | — | PyInstaller/MSIX in Phase 9 |
