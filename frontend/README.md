# Still — image workspace

React frontend for the existing FastAPI upload service. Includes token connection,
raw-body uploads with transfer progress, drag-and-drop/batch selection, thumbnail
polling and failure placeholders, search/status filters, existing-reference lookup,
and authenticated original downloads. Tokens are kept in memory, never browser
storage. Disconnect clears the session and aborts active requests.

## Run locally

Start PostgreSQL, apply backend migrations, and run the backend API on port 8000
and thumbnail worker as described in the project walkthrough. Configure a bearer
token hash and matching quota row on the backend. Use the original token in the UI.

From `frontend`:

```sh
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to `http://127.0.0.1:8000`, so
browser requests stay on the frontend origin. To use a different local backend,
set `BACKEND_URL` in `frontend/.env.local` (e.g. `http://127.0.0.1:8001`). Restart
Vite after changing it. Do not commit credentials or prefill a token in build variables.

The backend has no list endpoint: the gallery contains this session's uploads and
references opened explicitly by ID. Refresh clears the token and gallery. This is
API-token authentication; there is no signup/login or delete UI because those
backend endpoints do not exist. Status filters keep polling hidden pending cards.

## Validate and build

```sh
npm test
npm run build
```

API tests use mocked HTTP/XHR and cover credentials, raw uploads, progress,
cancellation, and error messages. They do not replace browser integration testing.

The Vite development proxy is not part of the production bundle. Serve production
`dist/` behind an HTTPS reverse proxy routing `/api` to FastAPI, or set the
build-time `VITE_API_BASE_URL` to the full HTTPS API prefix (ending in `/api/v1`)
and configure backend CORS for that exact frontend origin. Never serve a token
workflow over public plain HTTP. No hosting deployment is configured here.

Manual check: connect with a valid token; upload a real image; stop/start the
worker to see pending then ready; download and compare the original; reopen a
reference by ID; test a second user's access, bad tokens, exhausted quota, and a
burst beyond the upload limit. Inspect at narrow and wide viewport sizes.
