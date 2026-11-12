# Security Policy

MATRIX is a local-first assistant that can read files, launch allowlisted applications and run allowlisted
commands — always behind a server-side policy engine and exact owner approval for high-risk actions.

- **Full model, controls and residual risks:** [`docs/SECURITY.md`](docs/SECURITY.md)
- **Acceptance evidence:** [`docs/ACCEPTANCE_REPORT.md`](docs/ACCEPTANCE_REPORT.md)
- **Supported version:** `0.1.x` on the `main` branch.

## Reporting a vulnerability

Please use a **private GitHub security advisory** on `ayaanraza2008/matrix-ai` (Security → Advisories →
Report a vulnerability), or contact the owner through the GitHub profile. Do not open a public issue with
reproduction details for an unfixed vulnerability.

Include: affected commit, component, reproduction steps, impact and any suggested fix. You can expect an
initial response within 7 days.

## Out of scope

- Vulnerabilities that require an already-compromised OS account or physical access to an unlocked PC.
- Rate-limit bypasses that require the owner token.
- Model hallucinations that do not result in a policy-bypassed action (the orchestrator is designed so
  model text alone cannot authorise anything).
- The Android scaffold not yet built — report those separately once it ships.
