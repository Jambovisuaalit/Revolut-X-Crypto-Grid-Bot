# Grid Console React basic setup (development only)

This is a **parallel** React + TypeScript + Vite scaffold. It does not replace the current production/preview static assets or modify the Python trading engine. The current Windows package continues to use `product_shell/assets/` until a separately reviewed integration step.

## Local commands

```sh
cd frontend
npm install
npm run typecheck
npm test
npm run build
```

The development server (`npm run dev`) serves the React shell at `http://127.0.0.1:5173`. API requests to `/api/runtime` require an explicitly configured **local** backend or will fail closed; no proxy to a public endpoint is configured. For standalone visual checks, expect the UI to show an unavailable/blocked state. No credentials are required.

## Security and rollout

- All account, trading, vault and payment functionality is intentionally absent.
- The existing static frontend, Tkinter desktop app, SQLite schema and GET-only Python server remain unchanged.
- No exchange or payment network calls are made by the React frontend.
- Next task: implement an explicit, tested localhost asset integration behind a controlled packaging build, and preserve old UI as rollback until Windows installer smoke tests pass.
- `LIVE_TRADING=DISABLED`; `SELLABLE_PRODUCT=NO_GO`.
