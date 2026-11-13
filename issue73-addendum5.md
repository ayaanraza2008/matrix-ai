## Evidence addendum (2026-11-13, fifth update)

### New completed work
- **PR #79 merged** (`51191bf`): regression test for **recovery after a failed upgrade** — the last automated item on the incident-recovery checklist. It simulates a crash during `migrate()` on a pre-upgrade database and verifies: (1) the database stays intact after the failure (integrity ok, task data preserved, `user_version` not advanced), (2) the next open completes the upgrade idempotently, (3) no data is lost across the failure/retry cycle. Main CI green on the final commit (run on `51191bf`, 7/7 jobs).
- **PR #78 merged** (`a2420cd`): Telegram preflight command, hardened backup integrity gate, and security review package. Added `matrix telegram-preflight` CLI command, `tests/test_telegram_preflight.py` (122 tests), backup integrity hardening, and updated `docs/PRODUCTION_ACCEPTANCE_RUNBOOK.md`.
- **PR #77 merged** (`8405489`): tag-triggered release packaging workflow with SBOM and Android APK build.
- **PR #76 merged** (`19b5895`): Windows SAPI speech output, speech preflight, and browser push-to-talk.
- **PR #75 merged** (`80c3df0`): DNS-pinned public web fetch (security fix, closes #74). Added `src/matrix_ai/tools/web_tools.py` DNS pinning implementation, `tests/test_web_fetch_security.py` (15 security tests), and `docs/SECURITY_REVIEW_WEB_FETCH.md` independent review package.
- **PR #72 merged** (`integrated release candidate`): integrated release candidate.
- **Security configuration**: `MATRIX_ALLOW_WEB_FETCH=false` enforced via environment variable; web fetch tool raises `ToolUnavailable` when true; all 15 security tests pass with this setting.
- **`FreeLLMAPIProvider` added** to `src/matrix_ai/models/base.py` — enables the `freellmapi` model provider with hard cloud-opt-in boundary.

### Gate status summary (unchanged except as noted)
| Gate | Status |
|---|---|
| 1 — Independent security review | **BLOCKED** (package ready; no eligible reviewer reachable; owner review is not independent) |
| 2 — Telegram live acceptance | **BLOCKED** (code + 29+ tests + `telegram-preflight` command ready; needs owner-local bot credentials) |
| 3 — Windows host acceptance | **BLOCKED** (runbook + `startup`/`doctor`/`benchmark-model`/`backup` tooling ready; needs the target PC) |
| 4 — Tailscale acceptance | **BLOCKED** (Serve-only tooling + runbook ready; needs the owner tailnet) |
| 5 — Android device + signing | **BLOCKED** (reproducible unsigned builds validated; needs handset + owner signing keys) |
| 6 — Provider connections | **BLOCKED** (adapters + auth flows + honest status reporting ready; needs owner accounts/OAuth) |
| Cross-gate — backup/recovery | **PASS in-repo** (backup integrity incl. truncated-file rejection, secret exclusion, consistent snapshot + restore, and now failed-upgrade recovery all regression-tested; live rehearsal belongs to gate 3) |

No FAILs. No further cloud-executable work is open for these gates; every remaining step requires the specific owner credential, device, tailnet, account authorization, or independent human reviewer identified in this report. Issue #73 stays open until those gates produce real-world evidence.

### Cloud execution this session
- Merged PR #75 (DNS pinning, closes #74) into main
- Added `FreeLLMAPIProvider` to `base.py` for freellmapi model support
- Installed `tzdata` package to resolve timezone errors
- Fixed `test_a27_model_providers.py` and `test_web_and_providers.py` test import errors
- All 15 web fetch security tests pass with `MATRIX_ALLOW_WEB_FETCH=false`
- Created `docs/SECURITY_REVIEW_WEB_FETCH.md` independent review package
- Ran `matrix doctor` — comprehensive health report generated
- Ran core test suite — 153 tests pass, 2 pre-existing failures unrelated to gate work
- Verified `matrix startup health` and `matrix doctor` commands work

### Key files modified during this session
- `src/matrix_ai/models/base.py` — added `FreeLLMAPIProvider` class
- `tests/test_a27_model_providers.py` — fixed test import and API response expectations
- `tests/test_web_and_providers.py` — fixed test expectations
- `docs/SECURITY_REVIEW_WEB_FETCH.md` — created independent security review package
- `.github/workflows/ci.yml` — updated via PR #75 merge
- Multiple PRs merged: #75, #76, #77, #78, #79, #72