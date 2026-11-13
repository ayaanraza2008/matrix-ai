# Astryx Studio frontend

MATRIX keeps its existing vanilla-JavaScript dashboard at `/`. The optional Astryx/React 19 frontend is built separately from `frontend/` and served at `/studio/`.

## Build

Requirements: Node.js 22+ and npm.

```bash
cd frontend
npm install
npm run build
cd ..
```

The build writes `frontend/dist`. Start MATRIX normally with `matrix serve`; when that directory exists, FastAPI serves the studio at `http://127.0.0.1:8765/studio/`. If the frontend has not been built, the classic dashboard and API continue to work normally.

For local frontend development, run the MATRIX API on port 8765 and use `npm run dev` from `frontend/`. Vite proxies `/api` requests to the local API.

## Integration boundary

- Uses the existing `/api/health`, `/api/tasks`, and `/api/chat` endpoints.
- Reuses the browser's existing `matrix_token` key so a user already authenticated in the classic dashboard does not need a second login.
- Private API requests carry the bearer token; the health endpoint remains public as before.
- Does not execute tools, shell commands, file operations, or approvals in the browser.
- The Python orchestrator, typed tool registry, central policy/approval/verification path, worker pool, SQLite memory, and Android scaffold remain authoritative and unchanged.
- The classic dashboard remains available at `/` for all existing screens and controls.

Astryx is MIT-licensed and its React peer dependency is React 19+. The frontend pins Astryx core and neutral theme to 0.7.15.
