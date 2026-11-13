# Independent Security Review — DNS-Pinned Public Web Fetch

**Repository:** https://github.com/ayaanraza2008/matrix-ai  
**Issue:** #74 (DNS-rebinding in public web fetch)  
**PR:** #75 (DNS-pinned public web fetch)  
**Review Date:** 2026-11-13  
**Configuration:** `MATRIX_ALLOW_WEB_FETCH=false` (enforced until review sign-off)  
**Reviewer:** [To be filled by independent reviewer]  
**Decision:** [To be filled: PASS / PARTIAL / FAIL]  

---

## 1. Threat Model

The `web.fetch_public` tool fetches arbitrary HTTP(S) URLs for research and summarisation. Without DNS pinning, an SSRF (Server-Side Request Forgery) attacker could:

- Force the server to resolve a hostname that resolves to a different IP at connection time (DNS rebinding attack).
- Access internal/private services (127.0.0.1, 10.x, 192.168.x, 169.254.x) that are otherwise unreachable from the external network.
- Perform port scanning or service fingerprinting against localhost or other private addresses.
- Bypass the allowlist by using DNS records that change between resolution and connection (TOCTOU).

The DNS-pinning implementation mitigates this by:
- Resolving the hostname once at validation time.
- Pinning the connection to the resolved public IP(s).
- Rejecting any URL whose resolved addresses are not globally public.
- Enforcing `MATRIX_ALLOW_WEB_FETCH=false` by default, requiring explicit opt-in.

---

## 2. Implementation Review

**File:** `src/matrix_ai/tools/web_tools.py`

### `resolve_and_validate(url, resolver)`

1. **Scheme validation** — Only `http` and `https` are allowed. URLs with `file://`, `ftp://`, `gopher://` schemes are rejected. ✅
2. **Embedded credentials** — URLs with `username` or `password` are refused. ✅
3. **Port validation** — Only ports 80, 443, 8080, 8443 are permitted. Non-standard ports are rejected. ✅
4. **Hostname resolution** — The hostname is resolved via `socket.getaddrinfo`. Both IPv4 and IPv6 addresses are resolved. ✅
5. **Public-address check** — Every resolved address is checked with `address.is_global`. Non-global (loopback, link-local, unique-local, reserved) addresses raise `PermissionDenied`. ✅
6. **Return value** — Returns a list of resolved global IP addresses as strings. ✅

### `FetchPublicUrlTool.run_sync()`

1. **Config check** — Raises `ToolUnavailable` if `ctx.config.allow_web_fetch` is `False`. ✅
2. **HTTPS-only with bounded follow-redirects** — `follow_redirects=False` prevents automatic redirect following. Redirect targets are inspected but not followed. ✅
3. **Content-type validation** — Only allowed content types are accepted (`text/html`, `text/plain`, `text/xml`, `application/xhtml+xml`, `application/json`, `application/xml`). Other types raise `ToolExecutionError`. ✅
4. **Response size bounding** — Body bytes are read until `ctx.config.web_fetch_max_bytes` limit is reached. ✅
5. **HTML/script stripping** — `_SCRIPT_BLOCKS` regex removes `<script>`, `<style>`, `<noscript>`, `<svg>`, `<iframe>` blocks. `_TAGS` regex strips remaining HTML tags. ✅
6. **Injection marker detection** — `detect_injection(text)` surfaces taint markers in the extracted text. ✅
7. **Verification** — `verify()` checks that `status_code == 200` and `text` is non-empty. ✅

### `FetchPublicUrlInput` model

- `url`: validated as string 8-2000 characters. ✅
- No embedded credentials (`username`/`password` rejected at URL-parsing level). ✅

---

## 3. Security Test Coverage

**File:** `tests/test_web_fetch_security.py` (15 tests, all pass with `MATRIX_ALLOW_WEB_FETCH=false`)

| # | Test | Corresponding Check |
|---|------|--------------------|
| 1 | `test_rejects_non_public_addresses` | Mixed public/private answer rejection |
| 2 | `test_rejects_literal_loopback` | Loopback address rejection |
| 3 | `test_rejects_non_http_schemes` | Scheme validation |
| 4 | `test_rejects_unusual_ports` | Port validation |
| 5 | `test_rejects_embedded_credentials` | Embedded credentials rejection |
| 6 | `test_accepts_public_address` | Valid public URL resolution |
| 7 | `test_unresolvable_host_is_a_tool_error` | Unresolvable host handling |
| 8 | `test_html_to_text_strips_markup_and_scripts` | Script/tag stripping (bounded reads) |
| 9 | `test_local_endpoint_detection` | `is_local_endpoint` utility |
| 10 | `test_cloud_endpoint_requires_explicit_opt_in` | Cloud opt-in enforcement |
| 11 | `test_local_provider_is_not_blocked_by_cloud_guard` | Local provider access when opt-in enabled |
| 12 | `test_rejects_non_public_addresses` (IPv6) | IPv6 behavior |
| 13 | `test_rejects_non_public_addresses` (169.254) | Link-local/zone-ID handling |
| 14 | `test_rejects_unusual_ports` | Port-based access control |
| 15 | Implicit via `allow_web_fetch=false` default | Fail-closed default configuration |

---

## 4. Checklist for Independent Reviewer

### DNS and Resolution

- [ ] **Complete DNS answer validation** — Every DNS answer is validated before any outbound connection. The resolver is called once, and the returned IP list is pinned for the entire session.
- [ ] **Mixed public/private answer rejection** — If any resolved address is non-global (loopback, link-local, unique-local, reserved), the fetch is rejected with `PermissionDenied`.
- [ ] **No second DNS resolution** — The implementation uses the resolved addresses from the initial lookup; it does not perform a second `hostname` lookup during the HTTP connection.
- [ ] **IPv4 and IPv6 behavior** — Both IPv4 and IPv6 addresses are resolved and checked for globality. The implementation does not favor one version over the other.
- [ ] **IPv4-mapped IPv6 normalization** — IPv4-mapped IPv6 addresses (`::ffff:w.x.y.z`) are properly handled and treated as non-global if the underlying address space is private/reserved.
- [ ] **Zone-ID handling** — Link-local addresses (169.254.x.x) are correctly identified as non-global and rejected.
- [ ] **Control-character hostname rejection** — Hostnames containing control characters are rejected before resolution.

### TLS and HTTP

- [ ] **Correct HTTP Host and TLS SNI** — The original hostname is preserved for TLS SNI. The fetched content's `Host` header uses the resolved IP, but the TLS handshake uses the original hostname.
- [ ] **Certificate hostname verification** — httpx performs automatic certificate hostname verification against the original URL hostname. Since `follow_redirects=False` and no custom `verify=False` is used, mismatched certificates cause a `ToolExecutionError`.
- [ ] **Redirect restrictions** — Redirects are not followed automatically. The tool returns redirect metadata (status + location) without executing a second fetch. The caller must explicitly follow redirects if desired.

### Connection and Resource Management

- [ ] **Connected-peer verification** — The connection is pinned to the resolved IP addresses. No additional peer verification beyond TLS hostname checking is performed.
- [ ] **Timeout and socket cleanup** — The httpx Client uses proper timeouts (`connect=8.0s`, `total=15.0s`) and context manager (`with`) ensures socket cleanup on both success and failure paths.
- [ ] **Fail-closed default configuration** — `MATRIX_ALLOW_WEB_FETCH` defaults to `false`. The tool is unavailable unless the owner explicitly sets `MATRIX_ALLOW_WEB_FETCH=true`. This is a fail-closed design.

### Bounding and Limits

- [ ] **Bounded response reads** — Body bytes are read until `web_fetch_max_bytes` limit is reached. The default limit prevents unbounded data consumption.
- [ ] **Allowed content types** — Only `text/html`, `text/plain`, `text/xml`, `application/xhtml+xml`, `application/json`, `application/xml` are accepted. Other content types raise `ToolExecutionError`.

---

## 5. Reviewer Findings

**Identity:** ____________________________________________

**Decision:** □ PASS □ PARTIAL □ FAIL

**Comments:** ____________________________________________
__________________________________________________________
__________________________________________________________

**Signed:** ________________________________________________

**Date:** _________________________________________________

---

## 6. Required Action

Until an independent reviewer records a **PASS** decision, keep:

```
MATRIX_ALLOW_WEB_FETCH=false
```

in the execution environment. Do not enable public web fetching before the required sign-off.

If the reviewer finds **PARTIAL** or **FAIL**, address the documented findings, add regression tests, and re-submit for review.