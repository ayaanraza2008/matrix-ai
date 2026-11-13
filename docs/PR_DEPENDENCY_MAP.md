# MATRIX pull-request dependency map

**Snapshot:** 2026-11-13. Re-check GitHub before acting; PR state, head commits, and CI can change.

This document records the observed base/head relationships for the open implementation PRs. A branch being mergeable in GitHub means the Git graph can be merged; it does not mean the feature is safe to merge or that its prerequisites are integrated.

## Foundational remote-control chain

```text
main
 └─ #14 local Whisper/Piper engines
     └─ #16 private remote access + bounded jobs
         └─ #18 health monitoring + Telegram alerts
             └─ #19 private two-way Telegram bridge
                 ├─ #20 Android CI/security hardening
                 └─ #21 Telegram rate-limit hardening
                     └─ #22 initial persistent goal supervisor
```

The core PR descriptions report successful CI runs on the recorded heads for #14, #16, #18, #19, #20, #21, and #22. This is branch-level CI evidence only, not integrated-main or target-PC acceptance.

## Goal execution and overlapping alternatives

Observed relationships:

- #29 (per-task dependency enforcement) is based on #22.
- #30 (persistent goal DAG plan persistence) is also based on #22.
- #31 (persistent DAG execution) is based on #29.
- #32 (bounded goal-node scheduler) is based on #30.
- #34 (pause/resume) is based on #32.
- #33 (YouTube actions) is based on #31.
- #35 (pause/resume plus GitHub/weather actions) is based on #33.
- #37 (independent goal criteria verification) is based on #35.
- #38 (social connectors) is based on #35.
- #39 → #40 → #41 → #44 is the Instagram image publishing, WhatsApp inbound, WhatsApp template messaging, and security-consolidation chain.
- #46 (criteria review + owner confirmation) is based on #29.
- #47 (persistent multi-task goal supervisor) is based on #46.
- #50 (independent evidence-based criteria verifier) is also based on #29.
- #49 (recurring goal lifecycle controls) is based on #18.

**Do not merge every branch in this list blindly.** #30/#32 and #29/#31 are overlapping/related goal execution tracks; #34 and #35 are separate pause/resume tracks; #37, #46, and #50 contain overlapping goal-verification approaches. Review the actual diffs and tests, select one coherent implementation path, port any unique fixes, and re-run integration tests on the resulting candidate. In particular, #47 depends on #46 and does not automatically include #50.

## Parallel integrations and add-ons

- #24 (Open-Meteo) is based on #21; #26 (GitHub actions) is based on #24.
- #28 (Android Keystore) and #27 (DNS-pinned web fetch) are based on #22.
- #36 (isolated NVIDIA PersonaPlex/Nemotron setup) is based directly on main.
- #42 (Microsoft AI Engineering Coach reference add-on) is based directly on main; #45 (native session analytics) is based on #42; #48 (coding-agent adapters/dashboard) is based on #45.
- #43 (Vercel Agent Skills engine) is based directly on main.
- #25 (production-readiness evidence documentation) is based on #22.
- #52 (this production acceptance plan) is documentation-only and based directly on main.

## CI evidence checked in this snapshot

Successful GitHub Actions runs were found for the recorded heads of #14, #16, #18, #19, #20, #21, #22, #29, #31, #33, #38, #43, #47, and #50. The corresponding run IDs are discoverable from each PR head in GitHub Actions. This list is not exhaustive for every open PR and does not establish that the current head still matches the checked SHA.

No CI status was returned for the current main SHA at the time of this snapshot. Re-run CI on the integrated candidate and require all blocking jobs to pass.

## Safe integration sequence

1. Review and integrate the foundational chain #14 → #16 → #18 → #19, then reconcile #20 and #21 against it.
2. Integrate #22 and #29 only after their bases are in main; verify the per-task dependency contract end-to-end.
3. Compare #30/#32 against #31; choose one persistent goal DAG execution path rather than stacking overlapping schedulers.
4. Compare #34/#35 for pause/resume and verify the selected path against goal dispatch, cancellation, restart recovery, and the YouTube/GitHub/weather features it carries.
5. Compare #37/#46/#50 and choose one explicit criteria-verification and owner-confirmation contract; then review #47 against that chosen contract.
6. Integrate provider branches in dependency order, re-running the full suite after each substantial integration.
7. Review independent branches #36, #42/#45/#48, #43, and #49 separately; merge only after compatibility, security, licence, and CI review.
8. Keep all PRs draft until code review, current CI, dependency order, and documented acceptance evidence are satisfied.

## Non-negotiable release checks

- A passing PR CI run is not proof of live Telegram, OAuth, Android hardware, voice, browser, or Windows installer acceptance.
- Do not merge a feature PR solely because `mergeable=true`.
- Do not resolve overlapping PRs by force-pushing or rewriting shared branches without a reviewed migration plan.
- Preserve owner approval, least privilege, audit evidence, and emergency stop across every integration.
