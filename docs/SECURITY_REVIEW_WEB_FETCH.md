# Independent security review package: DNS-pinned public web fetch

Purpose: give a qualified reviewer who did not author this change everything
needed to perform and record an independent sign-off in one sitting.

- Implementation: `src/matrix_ai/tools/web_tools.py` (merged to `main` via PR #75,
  plus hardening commit `6316959`).
- Tests: `tests/test_web_fetch_security.py` (15 tests), plus default-off policy
  tests in `tests/test_web_and_providers.py`
  (`test_public_web_fetch_is_disabled_by_default`,
  `test_public_web_fetch_requires_explicit_opt_in`).
- Tracking: issue #74 (closed by the PR #75 merge), acceptance gate in issue #73.
- CI evidence: run [43451277034](https://github.com/ayaanraza2008/matrix-ai/actions/runs/43451277034)
  (7/7 jobs) on the final PR head; main-branch CI green on the merge commit.

## Review trail (verbatim, no invented approvals)

1. Author-side review of the implementation was posted on PR #75 by the
   repository owner account (`ayaanraza2008`), state `COMMENTED`. **This is not
   an independent approval** — the same account authored the commits.
2. A second agent review pass was performed on the merged `main` state (this
   document, 2026-11-13). Result: no new findings; two earlier hardening commits
   (IPv4-mapped IPv6 normalization; control-character hostname guard) had
   already addressed the only notable gaps.
3. What is still required for closure: a review decision from a reviewer who
   did not author the change. See "How to sign off" below.

## Threat model being addressed

An attacker who controls DNS (or a domain) must not be able to make MATRIX
connect to internal/private services (SSRF/DNS rebinding), including by
re-binding DNS between validation and connection, mixing private answers into
public lookups, or redirecting after the fetch.

## Requirement checklist (implementation → test)

| Requirement | Implementation | Regression test |
|---|---|---|
| Every DNS answer validated | `resolve_and_validate` loops all answers, `is_global` gate | `test_resolver_rejects_private_and_returns_all_public_answers` |
| Mixed public/private answers fail closed | raises on any non-global answer | `test_resolver_rejects_mixed_public_and_private_answers` |
| Connect to validated numeric IPs only | `fetch_pinned` iterates pre-validated list | `test_fetch_connects_to_numeric_validated_ipv4_or_ipv6` |
| No second hostname resolution | direct `socket.connect((ip, port))`; `getaddrinfo` monkeypatched to raise | `test_pinned_fetch_never_resolves_hostname_again` |
| Peer verification after connect | `getpeername()` compared to validated IP | `test_peer_mismatch_is_rejected` |
| TLS identity + SNI keep original hostname | `wrap_socket(raw_sock, server_hostname=host)` with default verification | `test_https_preserves_hostname_for_sni_and_certificate_verification` |
| HTTP Host header keeps original hostname | `Host:` built from the original hostname, IDNA-validated | request-content assertions in `test_fetch_connects_to_numeric_validated_ipv4_or_ipv6` |
| IPv4/IPv6 targets correct | AF_INET 2-tuple / AF_INET6 4-tuple with scope 0 | parametrized connect-target test |
| IPv4-mapped IPv6 normalized | `::ffff:0:0/96` → embedded IPv4 before `is_global` (CVE-2024-4032 independence) | `test_resolver_normalizes_ipv4_mapped_ipv6_to_plain_ipv4`, `test_resolver_rejects_mapped_private_ipv4_via_ipv6_form`, `test_mapped_public_ipv6_connects_over_af_INET_with_matching_peer`, `test_mapped_private_peer_form_is_rejected` |
| Zone identifiers handled | `%zone` stripped before parsing; link-local zones rejected by `is_global` | covered by rejection tests |
| Redirects never followed | `http.client` never auto-follows; non-200 returns `location` metadata only | `test_redirect_is_returned_not_followed` |
| Bounded response reads | `response.read(max_bytes + 1)`; header line length capped by `http.client` | `test_response_read_is_bounded_to_limit_plus_one` |
| Timeouts | one socket timeout covering connect, TLS handshake and reads | manual/property — no regression test yet |
| Socket cleanup on all paths | `finally` closes response + connected socket, including TLS-wrap failure | asserted indirectly by fake-socket `closed` flags; real-socket cleanup not asserted |
| Control characters in hostname | rejected before request construction | `test_pinned_fetch_rejects_hostname_with_control_characters` |
| Feature disabled by default | `MATRIX_ALLOW_WEB_FETCH=false` gate in `run_sync` | `test_public_web_fetch_is_disabled_by_default`, `test_public_web_fetch_requires_explicit_opt_in` |

## Known limitations to weigh in the review decision (not findings)

- The timeout and real-socket-cleanup rows above are verified by construction
  and by fake-socket flags; a reviewer may consider them adequately covered or
  request a real-socket test.
- A global IPv6 literal with a zone suffix is silently de-scoped (connecting to
  the global address without the zone). Safe, but a reviewer may want it
  rejected outright for strictness.
- `is_global` depends on the interpreter's `ipaddress` semantics; the
  normalization removes the known divergent case (mapped IPv4).

## How to sign off

1. Check out `main` at or after `8405489`.
2. Run: `python -m pytest tests/test_web_fetch_security.py tests/test_web_and_providers.py -q`
3. Read `src/matrix_ai/tools/web_tools.py` end to end against the checklist.
4. Either (a) approve/record findings on issue #73 or a new issue referencing
   `web_tools.py`, or (b) open a review on a no-op documentation PR.
5. Record: reviewer identity (or explicit anonymity), date, decision
   (approved / changes requested), and any findings with severity.

`MATRIX_ALLOW_WEB_FETCH` stays `false` in production configuration until this
sign-off is recorded. The default remains off regardless.
