# CLAUDE.md — conventions for AI agents working in this repo

Read `memory.md` first: it is the shared state and the workflow (builder checklist, reviewer checklist, owner decisions). Humans: see `CONTRIBUTING.md` (same rules).

## Product and copy rule

Home School is **home tutoring, not homeschooling**. Never write "homeschool"/"homeschooling" in user-facing copy, store listings, docs or code comments describing the product. Say "home tutoring", "tutor", "lessons at home".

## Stack

- Mobile: Flutter apps `apps/parent`, `apps/teacher`, shared Dart in `packages/flutter_shared`.
- Admin: Next.js web app `apps/admin` (ADR 0004). `spikes/` is throwaway; the real admin app is F06.
- Core service: FastAPI in `services/core`, hosted on Fly.io (ADR 0003), job runner per ADR 0002.
- Backend platform: **Appwrite Cloud** (not Supabase). Definition in `appwrite/` (ADR 0012).
- Layout and which packages are real: `packages.json`. Planned packages are skipped by `make test` until their ticket scaffolds them.

## Commands

| Command | What it does |
|---|---|
| `make setup` | `npm ci` in apps/admin (a clean checkout needs it before `make lint`; see CONTRIBUTING.md "Setup from a clean checkout") |
| `make test` | runs every implemented package's tests; planned packages and missing tools are skipped with a message (`HS_STRICT=1` makes a missing tool fail; `HS_SKIP=id,id` skips ids) |
| `make lint` | layout guard, secret scan, per-package lint |
| `make layout` / `make secrets` | one check only |
| `make hooks` | install pre-commit hooks |
| `make tools` | list installed tools |

Per-package test commands are in each package README and in `memory.md` "How to run tests". Add a row there for every new package.

## Rules

1. **One ticket = one PR.** Branch `<id>-<slug>` (e.g. `f03-core-service`), PR title starts with the backlog ID, PR body has `Closes #NN` and ends with the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Commit messages start with the ID and end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. One ticket per PR; keep the diff to that ticket.
2. **Tests first.** Write tests that encode every acceptance criterion and edge case, watch them fail, then implement. Tests run offline and from a clean checkout. Never fake a test to make `make test` pass; skip honestly via `packages.json`.
3. **Adapters and fakes for external services.** Appwrite, MoMo/card gateways, FCM, email, SMS, Maps, Sentry sit behind interfaces with a fake implementation used in tests. No test calls a real service.
4. **No secrets.** Never print, log or commit credentials. `/Users/mat/Workspace/.workspace_env` is never read or copied. `.env*` is git-ignored except `.env.example`. `make secrets` and gitleaks guard this; do not weaken them.
5. **The live Appwrite project is read-only.** Never create, update or delete anything there. Build the schema fresh from tickets and System Requirements; record diffs for the owner. Anything needing a real deployment goes under "Blocked on owner" in `memory.md`. Do not deploy, and do not pick payment, email or other providers.
6. **Money is integer UGX** (no floats, no cents). **Timestamps are stored in UTC and displayed in Africa/Kampala** (UTC+3, no DST).
7. **Appwrite limits learned (ADRs 0002, 0004, 0012):** list `total` is capped at 5,000 (paginate with cursors, never trust `total` above it); no conditional update, no triggers, no exclusion constraints, so enforce uniqueness and atomicity in the core service/job runner; role labels are alphanumeric only (no underscores); permissions per ADR 0012.
8. **New packages** go only under `apps/`, `packages/`, `services/` and must be registered in `packages.json` with a README (`## Purpose`, `## Test command`). `make lint` fails otherwise.
9. **memory.md** is committed straight to `main` (`memory: ...`), never inside a ticket PR. Follow the hand-off protocol there. Builders do not open PRs; the reviewer does.
10. Branch protection and required checks are an owner setting (see "Blocked on owner" in `memory.md`); never try to change repository settings.
