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

## Setup from a clean checkout (green baseline)

A fresh clone or `git worktree` has no installed dependencies. These commands, in this order, give a green baseline (`HS_STRICT=1` is what CI uses):

```
make setup                                      # npm ci in apps/admin (needs node/npm and network, or a warm npm cache)
make lint                                       # layout, secret scan, ruff, per-package lint
make test                                       # every implemented package (apps/admin re-runs npm ci itself)
python3 -m unittest discover -s scripts/tests   # root tooling tests (about 1 minute)
uvx pre-commit run --all-files                  # optional; same checks as the hooks
```

Without `make setup`, `make lint` skips apps/admin with `SKIP apps/admin: not set up (missing node_modules); run npm ci in apps/admin` (fails under `HS_STRICT=1`), and the prettier/eslint pre-commit hooks fail with `apps/admin needs npm ci first`. Tools that are not installed (flutter, uv, ruff, pre-commit, gitleaks) are skipped with a message by `make`.

### Troubleshooting

- **`npm ci` fails with EACCES / root-owned files in `~/.npm`** (npm was once run with sudo): do not sudo again. Use a scratch cache with an absolute path: `export npm_config_cache=/tmp/hs-npm-cache` (a relative path resolves inside `apps/admin` and trips the layout guard), or fix once with `sudo chown -R "$(id -u):$(id -g)" ~/.npm`.
- **`npx tsc` tries to download packages or says `tsc` is not the compiler**: `apps/admin/node_modules` is missing. Run `make setup`; never run `npx tsc` before `npm ci`.
- **pre-commit "git" errors in a worktree or inside a git hook**: pre-commit and the git-calling scripts read `GIT_DIR`, `GIT_INDEX_FILE` and `GIT_WORK_TREE` from the environment. If a parent process exported them, run `env -u GIT_DIR -u GIT_INDEX_FILE -u GIT_WORK_TREE uvx pre-commit run --all-files`. Hooks installed with `make hooks` live in the shared `.git/hooks` of the main checkout and apply to every worktree; if `core.hooksPath` is set, pre-commit refuses to install (`git config --unset-all core.hooksPath`). `make lint` runs the same checks without pre-commit.
- **No Xcode / Docker**: neither is needed for `make test` or `make lint`. Flutter packages are planned (F05) and skip with a message.

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
- CI/CD: `.github/workflows/` (`ci.yml` required check `ci-ok`; staging deploy and production promotion stay off until the owner sets `DEPLOY_ENABLED`). Rules and owner checklist: `docs/ops/ci.md`, `docs/ops/deploy.md`. Workflow rules are tested: `python3 scripts/ci_lint.py`.
- CODEOWNERS: `@Matt-Murungi` owns everything.

## Tests must not read the wall clock

Use the injected clock (the `clock=` parameter of the service, the `Clock`/`FakeClock` helpers in `services/core/tests/`), never `datetime.now()`/`time.time()` in a test or an unparameterised `build_*` wiring. A test that passes at noon and fails at 23:30 Kampala (N01 quiet hours 21:00-07:00) broke `main` once. `python3 scripts/clock_matrix.py` runs the suites under frozen clocks (night, Sunday, month end, year end, leap day, UTC date != Kampala date) and names every test whose result depends on the clock; a weekly workflow runs it, and the core CI job runs two clocks on the notifications, jobs, ledger and permissions tests. Run it locally after touching time-sensitive code: `python3 scripts/clock_matrix.py --targets core --clocks quick`.

## Rules that matter

- Never commit secrets; `.env*` is ignored except `.env.example`. Never use credentials from outside the repo in code or tests.
- The live Appwrite project is read-only for agents; use fakes or a throwaway project.
- Money is integer UGX. Timestamps are stored in UTC and shown in Africa/Kampala.
- Appwrite limits (ADRs 0002, 0004, 0012): list totals cap at 5,000; no conditional update, triggers or exclusion constraints.
- Positioning copy rule: home tutoring, not homeschooling (never write "homeschool" in user-facing copy).
- Decisions are ADRs in `docs/adr` (`python3 docs/adr/check-adrs.py`).
