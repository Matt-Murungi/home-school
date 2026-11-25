# Contributing

Applies to humans and AI agents. Agents also read `CLAUDE.md` and `memory.md` (the workflow and hand-off protocol live there).

## Workflow

1. Pick one ticket (a GitHub issue; backlog ID such as `F01` in its first line). Check its "Blocked by" tickets are merged.
2. Branch from fresh `main`: `<id>-<slug>`, e.g. `f01-monorepo`. One ticket per branch and per PR.
3. Tests first: encode each acceptance criterion and edge case as a failing test, then implement. Tests run offline.
4. Use adapters with fakes for external services (Appwrite, MoMo, FCM, email, SMS, Maps, Sentry). No real calls in tests.
5. Run `make test` and `make lint` before pushing.
6. Commit messages start with the backlog ID (`F01: ...`). Agents end them with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
7. Humans open a PR titled `F01 · short title`; agent builders push the branch and hand off to the reviewer, who opens the PR (CLAUDE.md rule 9). The PR body links the issue with `Closes #27` (this is the issue-link convention) and uses the PR template.

## Layout

```
apps/parent  apps/teacher  apps/admin  packages/flutter_shared  services/core
appwrite/  docs/ (adr, product)  scripts/  spikes/ (throwaway)
```

New code goes only in these folders. A new package under `apps/`, `packages/` or `services/` needs an entry in `packages.json` and a README with `## Purpose` and `## Test command`; otherwise `make lint` fails with a message saying what to add.

## Tooling

- `make test`, `make lint`, `make tools`, `make hooks` (see `make help`). Missing tools (flutter, uv, node, pre-commit) are skipped with a clear message; CI sets `HS_STRICT=1` so they fail there.
- Pre-commit (`.pre-commit-config.yaml`): dart format, ruff check/format, prettier, eslint, gitleaks and `make lint`. `.editorconfig` sets whitespace.
- Secret scan: `make secrets` (`scripts/secret_scan.py`, tested) and `.gitleaks.toml`.
- CODEOWNERS: `@Matt-Murungi` owns everything.

## Rules that matter

- Never commit secrets; `.env*` is ignored except `.env.example`. Never use credentials from outside the repo in code or tests.
- The live Appwrite project is read-only for agents; use fakes or a throwaway project.
- Money is integer UGX. Timestamps are stored in UTC and shown in Africa/Kampala.
- Appwrite limits (ADRs 0002, 0004, 0012): list totals cap at 5,000; no conditional update, triggers or exclusion constraints.
- Positioning copy rule: home tutoring, not homeschooling (never write "homeschool" in user-facing copy).
- Decisions are ADRs in `docs/adr` (`python3 docs/adr/check-adrs.py`).
