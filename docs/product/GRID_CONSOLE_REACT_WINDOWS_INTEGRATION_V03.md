# Grid Console Desktop v0.3 — compiled React + Python localhost + Windows EXE

**Draft development integration. Not a customer release. No authenticated exchange calls.**

## Goal and scope

Integrate the **existing** React/TypeScript/Vite scaffold with the existing
GET-only Python localhost server and the existing Tkinter/PyInstaller/Inno Setup
Windows launcher. Preserve `GridExecutor`, `GridBotSupervisor`, SQLite schema,
kill-switch semantics and all other safety guards unchanged.

The desktop now starts `local_server(ui="react")` by default. The Python
server still supports `ui="legacy"` for controlled rollback and existing
CLI/contract tests; no automatic fallback is permitted in the desktop.

## Build and installation

Requirements: Windows x64, Python 3.11, Node.js 22, Inno Setup 6 and the
Python requirements, including PyInstaller 6.16.0. Node/npm are **build-time**
dependencies, not required for customers running the packaged EXE.

From the repository root:

```powershell
python -m pip install -r requirements.txt "pyinstaller==6.16.0"
./windows_distribution/build_grid_console_desktop.ps1
```

The build script runs `npm install` and `npm run build` in `frontend/`,
verifies `frontend/dist/index.html`, bundles the compiled
`frontend/dist` into `product_shell/react_dist` via PyInstaller
`--add-data`, builds an unsigned per-user Inno Setup installer and runs
two executable smoke tests: no configured database, then a disposable
synthetic SQLite with an active 3% risk lock and uncertain order.

The output is `installer-dist/GridConsoleDesktop-UNSIGNED-DEV-Setup.exe`.
**This is NOT signed and is NOT approved for customer installation.**

## HTTP and UI contract

- Python binds only to `127.0.0.1` on an ephemeral port.
- `GET /` serves the compiled React `index.html`.
- `GET /assets/<known-file>` serves only validated, allowlisted compiled
  Vite assets. The loader rejects remote HTML refs, development entrypoints,
  symlinks, oversized assets, missing references and unsupported extensions.
- `GET /api/status` retains the conservative shadow-only snapshot.
- `GET /api/runtime` reads the optional local supervisor SQLite via
  `mode=ro` and `PRAGMA query_only=ON`. It reports a **local** risk lock,
  heartbeat and redacted order/fill events.
- `POST`, `PUT`, `PATCH` and `DELETE` remain HTTP 405.
- Unknown routes, `/app.js`, `/style.css` and `/src/main.tsx`
  do not fall back to the old frontend when React mode is selected.
- Host/Origin/Sec-Fetch-Site checks, CSP, no-store and security headers
  remain enforced.

React validates the JSON provenance, `live_orders=DISABLED`,
`account_connection=NOT_CONNECTED` and
`reconciled_for_trading=false` before displaying any state.
Malformed or unreachable local data renders a blocked/unverified UI.
A risk lock is displayed as `RISK_LOCKED`/`TRIGGERED`.

## Windows GUI

The existing Tkinter controller still starts and owns the localhost server.
Its `Valitse valvojan paikallinen tietokanta` file picker reopens the
**React** viewer using the selected SQLite in read-only mode. Closing
the controller stops and closes the HTTP server. No automatic collector,
credential unlock, API request, order mutation or trade loop is started.

For offline testing only, the EXE accepts:

```powershell
GridConsoleDesktop.exe --self-test --report report.json
GridConsoleDesktop.exe --self-test --supervisor-db fixture.sqlite --report risk.json
```

The `--supervisor-db` argument is rejected outside `--self-test`.
The report includes only known safe state fields, never raw SQLite IDs,
paths, prices, balances or secrets.

## Tests and acceptance

1. `cd frontend; npm install; npm run typecheck; npm test; npm run build`.
2. `python -m unittest tests.test_react_built_bundle -v` after build.
3. `python -m unittest tests.test_react_server_integration tests.test_grid_console_desktop_gui tests.test_local_supervisor_view -v`.
4. `python -m unittest discover -s tests -v`.
5. Windows: run `build_grid_console_desktop.ps1`, then install the
   unsigned developer installer on an isolated test runner.
6. Installed EXE: `--self-test` verifies React hashed JS/CSS,
   GET-only API, local bind and NO_GO. A synthetic risk-locked SQLite
   verifies `RISK_LOCKED`, `TRIGGERED`, uncertain order count and
   redaction. Compare SHA256 of the SQLite file before and after.
7. Windows GUI: verify the window opens, receives WM_CLOSE and exits
   without leaving the process running.
8. Verify unsigned Authenticode release gate rejects both installer and
   EXE, and original process recovery/drawdown tests remain green.

## Common issues

- **TypeScript/Vitest TS2769:** use `defineConfig` from `vitest/config`,
  not `vite`, when specifying a `test` key.
- **Blank dashboard:** Vite output must be built and included in the
  PyInstaller bundle. Desktop deliberately fails startup if missing;
  it must not silently show the old frontend.
- **Missing CSS/JS:** `base: "./"` is required for relative hashed
  asset paths. The localhost server must serve these exact names.
- **Windows antivirus / SmartScreen:** the installer is unsigned and
  is development-only. Do not bypass warnings for end customers.
- **Port conflict:** the server requests an ephemeral port and binds
  only to loopback. Do not hardcode a shared port.
- **SQLite is locked/corrupt:** UI fails closed; it must not retry with
  write mode, repair data silently or reset a risk lock.
- **False account balance:** the UI has no authenticated account
  integration. Local SQLite records are not exchange reconciliation.

## Dependencies and remaining work

This stacked Draft PR depends on Draft PR #16 (React scaffold) and
Draft PR #15 (local SQLite observability). PR #16 initially had failing
TypeScript checks; the `vitest/config` and strict narrowing fixes
are included here. This PR is not ready until all current CI jobs pass.

Next separate work: versioned supervisor event history, tested SQLite
backup/restore, a unified native WebView2 window (rather than the
current Tkinter launcher plus browser), and graphical secure onboarding.

**LIVE_TRADING=DISABLED · SELLABLE_PRODUCT=NO_GO · main unchanged.**
