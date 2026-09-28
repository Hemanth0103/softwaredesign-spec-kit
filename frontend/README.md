# Frontend development

T003 initializes React, TypeScript, Vite and the test tools. The page is a starter;
student chat, API integration and reviewer features are implemented by later tasks.

Use Node.js 22.22.2+, 24.15.0+ or 26.x within the declared engine range and npm 10 or 11. This setup was
validated with Node.js 26.8.2 and npm 11.19.1; `.nvmrc` selects that Node version.
Run these commands from `frontend/`:

```sh
npm ci
npm run test:install
npm run dev
```

`npm ci` installs the exact dependency tree in `package-lock.json`; the browser
installation downloads Chromium matching the locked Playwright version. On a clean
Linux/Docker test image, run `npx playwright install --with-deps chromium` to install
its OS dependencies too. The T005 test image does this during its build; see the
[beginner's Docker guide](../docs/docker.md) for container commands.

```sh
npm run lint
npm run typecheck
npm run build
npm run test
npm run test:e2e
npm run test:a11y
```

`test` runs Vitest once (use `test:watch` for watch mode). Vitest only discovers
`tests/unit/**/*.test.ts(x)`. Playwright runs `tests/e2e/` and `tests/a11y/` as
separate projects and starts/stops its own Vite server on port 4173. Leave that
port free and run the browser commands sequentially. Browser traces, screenshots
and video are disabled to avoid capturing future student chat content.

The three smoke tests verify React/TypeScript/DOM setup, browser boot and axe
integration. They do not validate chatbot behavior or establish WCAG 2.2 AA
compliance; later acceptance tests and manual accessibility review are required.

Dependencies are saved with exact versions; commit `package.json` and
`package-lock.json` together when intentionally updating packages. Generated
builds, reports and `node_modules/` are ignored. This application is private and
is not published to npm. Do not put secrets in frontend code or Vite variables.

## T017 API client

Call `loadApiClient()` from `src/api/client.ts` when initializing the future chat
UI, then call `client.ask(question, optionalAbortSignal)`. The client validates
requests and all five response outcomes at runtime. Catch `ServiceError` and use
its `code` or fixed safe `message`; server error bodies are never displayed.
Requests use no caching, omit credentials, reject redirects, and support timeouts
and cancellation. The client does not log or persist question content.

`public/runtime-config.json` defaults to `{"apiBaseUrl":"/api","timeoutMs":12000}`.
Configuration is fetched without caching before client creation. Deployments can
mount a replacement JSON file at `/srv/runtime-config.json` in the Caddy container
without rebuilding the image. Use a root-relative API path or an HTTPS URL and a
timeout between 1 and 120000 milliseconds. This public file must contain no secrets.
Caddy serves it with `Cache-Control: no-store`.

T017 validation: 26 unit tests passed, along with lint, TypeScript checks, and
the production build. The Docker runtime built successfully, its Caddy
configuration validated, and the runtime JSON was present in the image. No new
dependencies were required. Chat UI wiring and the live answers endpoint remain
in their later implementation tasks.
