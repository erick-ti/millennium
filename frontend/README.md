# Millennium frontend

Next.js (App Router) + React + TypeScript. It talks to the Django backend
through a same-origin `/api/*` proxy (configured in `next.config.ts`), so the
browser sees one origin and session cookies stay first-party.

For the full stack (backend + database + this app) run `make dev` from the repo
root. To work on just the frontend against a running backend:

```bash
npm install
npm run dev        # dev server at http://localhost:3000
npm run lint       # eslint
npm run typecheck  # tsc --noEmit over the whole tsconfig (covers *.test.tsx)
npm run test       # Vitest + React Testing Library
npm run build      # production build
npm run test:e2e   # Playwright smoke suite (see the root Makefile `make e2e`)
```

Set `BACKEND_URL` (see `.env.local.example`) to point the proxy at your backend;
under Docker Compose it defaults to the `backend` service.

The typed API client under `src/lib/api/generated/` is generated from the
backend's OpenAPI schema. Regenerate it from the repo root with
`make frontend-snapshot-schema` (refresh the schema) then `make frontend-gen-api`
(regenerate the client). Both the schema and the client are committed, and CI
fails if they drift from the backend serializers.
