# memory.md — shared state for the main agent and the reviewer

This file is the **only** channel between the main agent (builds) and the reviewer subagent (verifies, merges). A new session must be able to continue from this file alone. Keep it current: update it **before** handing off, and after every state change.

Repo: Matt-Murungi/home-school · Board: https://github.com/users/Matt-Murungi/projects/8 · Backlog IDs (D01, F03, M05…) are in each issue's first line.
Product docs: `/Users/mat/Library/Mobile Documents/com~apple~CloudDocs/Work Space/Projects/Home School/` (System Requirements v0.2 is the stack authority; stack is **Appwrite**, not Supabase).

## Owner decisions (24–25 Nov 2026) — binding

1. **Workflow:** main agent builds one ticket (tests first, then code), updates this file, hands to reviewer. Reviewer runs the tests, checks every acceptance criterion and edge case against the issue, then either sends it back with findings or opens a PR, reviews it, and merges it. Owner is consulted only at the end.
2. **Appwrite schema:** the live project already has 35 tables not in this repo. Build the schema fresh from the tickets and System Requirements §5, then record the **diff against the live project** in the section "Questions for the owner". Do not assume the live schema is right.
3. **Live Appwrite project is READ-ONLY.** Never create, update or delete anything there. Tests run offline against stand-ins. Anything that needs a real deployment goes in "Blocked on owner".
4. **Blocked work:** for tickets that need the owner's accounts, devices or decisions (Fly.io deploy, MoMo sandbox/credentials, card gateway, email domain, app stores, Codemagic, lawyer/DPO), build the foundation (interface/adapter, tests, docs), merge it, and list exactly what is missing in "Blocked on owner" and "Questions for the owner". Do not deploy, do not pick the provider.
5. **Stop condition:** when every ticket is merged or blocked, stop and bring the owner the consolidated "Questions for the owner" list.

## Rules for both agents

- One ticket = one branch `<id>-<slug>` (e.g. `f03-core-service`) = one PR into `main`. PR title starts with the backlog ID. PR body links the issue (`Closes #NN`) and ends with the line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Commit messages end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Never print, log or commit secrets. Credentials live in `/Users/mat/Workspace/.workspace_env` (never copy values into the repo or this file). `.env*` is git-ignored except `.env.example`.
- Do ticket order by "Blocked by" links in the issue. Never start a ticket whose blockers aren't merged (or marked blocked-foundation-merged).
- Tests must pass from a clean checkout using documented commands. Record the exact command per package under "How to run tests".
- Money is integer UGX. Timestamps stored UTC, shown in Africa/Kampala. Appwrite caps list totals at 5,000 (ADR 0004). Appwrite has no conditional update, triggers or exclusion constraints (ADRs 0002, 0012).
- Update the GitHub issue checkboxes and move nothing on the board by hand: the auto-add workflow handles the board; reviewer closes the issue by merging with `Closes #NN`.

## Hand-off protocol

**memory.md is committed straight to `main` (message `memory: …`), never inside a ticket PR.** This avoids merge conflicts. Always `git pull` before editing it, commit and push right after. Only one agent works at a time; the main agent waits for the reviewer's verdict before starting the next ticket.
The reviewer verifies in a separate worktree (`git worktree add <scratchpad>/review-<id> <branch>`), never in the main checkout, and removes it afterwards. A PR cannot be approved by its own author on GitHub, so the reviewer posts its review with `gh pr review --comment` and merges with `gh pr merge --squash --delete-branch`.

Each ticket has a block under "Ticket log" with a `Status`:
`todo` → `in-progress` (main) → `ready-for-review` (main) → `changes-requested` (reviewer) → `ready-for-review` … → `merged` (reviewer) | `blocked-foundation-merged`.
Main agent writes: what was built, files, test command + result, which acceptance criteria/edge cases map to which tests, deviations, open questions.
Reviewer writes: commands run, results, criterion-by-criterion verdict, findings, PR number, merge commit.

## Builder checklist (main agent role; a builder subagent follows this too)

1. `git pull` main; read this file; read the issue (`gh issue view N -R Matt-Murungi/home-school`) and the SR/PRD sections its "Spec refs" cite (product docs folder above). Confirm all "Blocked by" tickets are `merged` or `blocked-foundation-merged`; if not, stop and report.
2. Branch `<id>-<slug>` from fresh main. Work in a git worktree under the scratchpad if another agent could touch the main checkout.
3. **Tests first**: write tests that encode each acceptance criterion and each edge case in the issue; see them fail; then write the code until they pass. Keep tests runnable offline (no live Appwrite, no network, no real providers). Use stand-ins/fakes behind interfaces (adapter pattern) for Appwrite, MoMo, FCM, email, SMS, Maps, Sentry.
4. Put new packages where F01 (#27) defines: `apps/parent`, `apps/teacher`, `apps/admin`, `packages/flutter_shared`, `services/core`, `appwrite/`, `docs/`. Add each package's test command to "How to run tests". Keep the diff to one ticket.
5. Anything needing the owner's account, credentials, deployment or a product decision: build the interface, a fake implementation and tests, document the missing piece, add it to "Blocked on owner" and "Questions for the owner" (with your recommended default), and mark the ticket `blocked-foundation-merged` after merge. Never deploy, never call live services, never pick the payment gateway or email domain.
6. Commit on the branch (message starts with the backlog ID, ends with the Co-Authored-By line), push the branch. **Do not open the PR** (the reviewer does).
7. Update this file on main (`memory: ...` commit): ticket block with Status `ready-for-review`, what was built, files, test command and result, a table mapping each acceptance criterion and edge case to a test (or to "owner/blocked" with why), deviations, follow-ups. Enough detail for a fresh session to continue.
8. Report briefly: what was built, test result, anything blocked.

## Reviewer checklist

1. `git pull` main; read this file and the ticket block; read the issue.
2. Verify in a separate worktree: run the documented test commands from a clean checkout (record the real output); try to break the new logic with your own repro scripts; check every acceptance criterion and edge case against code and tests (a criterion without a test that proves it is a finding); check the diff for secrets, accidental live-service calls, committed `.env`, and that the diff is only this ticket.
3. If anything is amiss: ticket Status `changes-requested` with precise findings, commit to main, stop and report.
4. If fine: open PR (title starts with backlog ID; body: summary, evidence, caveats, `Closes #N`, the 🤖 line), review the diff, `gh pr review --comment`, `gh pr merge --squash --delete-branch`, confirm the issue closed, set Status `merged` (or `blocked-foundation-merged` for owner-blocked tickets) with PR number and commands/results, commit and push memory.md to main.
5. Report: verdict, commands and results, findings, PR URL.

## How to run tests

| Package | Command |
|---|---|
| spikes/admin-appwrite | `cd spikes/admin-appwrite && npm ci && npx tsc --noEmit && npm run lint && npm run build` |
| spikes/jobs-demo | `cd spikes/jobs-demo && uv run pytest -q` |
| docs/adr (ADR lint; reuse for every decision ticket) | `python3 docs/adr/check-adrs.py` (all ADRs) or `python3 docs/adr/check-adrs.py docs/adr/00NN-x.md`. Needs `# ADR NNNN — title` (number = file name), `- **Status:**` (Proposed/Accepted/Superseded/Deprecated), `- **Date:**`, `- **Ticket:**`, and `## Context`, `## Decision`, `## Consequences`. Exit 1 on failure. Stdlib only. |
| root tooling (F01) | `make test` (every implemented package per `packages.json`; planned ones skip with a message; `HS_STRICT=1` fails on missing tools, `HS_SKIP=id,id`) and `make lint` (layout guard + secret scan + package lint). F01 tests alone: `python3 -m unittest discover -s scripts/tests -v`. Stdlib only. |

(Add a row for each new package.)

## Ticket queue (MVP build order)

Source of truth for order and dependencies is each issue. Epics E00–E19, 135 tickets (issues #21–#155; epics #1–#20).
Suggested order: E00 decisions (D01–D03, D05; D04, D06 later/owner) → E01 foundations → E02 data model → E03 auth → E04 catalogue → E05 teacher onboarding → E06 parent onboarding → E07 plans → E08 payments → E09 matching → E10 scheduling → E11 session day → E12 logs → E13 safeguarding → E14 pay → E15 notifications → E16 admin → E17 compliance → E18 assessments → E19 quality/launch. Phase 2/3 tickets last.

## Ticket log

### D01 · Admin dashboard framework (#21)
- Status: **merged** (PR #156, squash commit 62e766b; issue #21 closed)
- Review: in a clean worktree `npm ci`, `npx tsc --noEmit`, `npm run lint`, `npm run build` all pass. Timing/seed scripts not re-run (read-only rule); recorded numbers checked against script logic and pager code (5,000 cap handled). Form validation, Kampala UTC+3 handling, server-only API key, no secrets tracked (`.env.local` ignored): OK.
- Follow-ups: link ADR 0004 from admin tickets; spike does not exercise Appwrite SSR auth/MFA (verify in F06/E03); owner sign-off on ADR still open.
- Built: ADR `docs/adr/0004-admin-dashboard-framework.md`; spike `spikes/admin-appwrite/` (paginated table + validated form, seed/timing/cleanup scripts).
- Evidence: 10,000 rows seeded into a temporary table in the live project's `main` database, timed (median 314–350 ms, worst 984 ms, every page <2 s), then the temp table was deleted; the 35 original tables untouched. **This was done before the read-only rule** — do not repeat it.
- Finding: Appwrite caps list `total` at 5,000 (recorded in the ADR).
- Open: owner sign-off on the ADR (status "Proposed"); spike's table-timing step needs a throwaway project to re-run.

### D02 · Job runner (#22)
- Status: **merged** (PR #157, squash commit 9eab5fc; issue #22 closed)
- Round 2 review: in a clean worktree `uv run pytest -q` -> 14 passed; `-k "concurrent or atomic"` x25 -> 25/25. Both orphan tests fail on ad19ad4 and pass now. Own repros (orphan younger than lease, two generations, orphan then handler failure then retry, orphan on attempt 2, chunk 1 after Continue, final-attempt orphan) all run the handler exactly once. All round-1 findings resolved.
- Acceptance: all met except staging hello-world (Blocked on owner).
- Follow-ups for F04: ADR test table omits `test_signed_tick_runs_due_jobs` (13 of 14 listed); a claimer whose `job_run` update stalls longer than the 5 min lease could double-run (late write after takeover) - verify the attempt row after the update or use a conditional-style check; re-verify live `job_run`/`job_attempt` columns against F02 schema; materialise watermark; indexed claimable query.
- Open: owner sign-off on ADR 0002; check Appwrite plan allows ~43,000 function runs/month.

### D03 · Hosting decisions (#23)
- Status: **blocked-foundation-merged** (PR #158 squash-merged, #23 closed; round-2 review: all four findings verified resolved, `python3 docs/adr/check-adrs.py` 4/4 ok, #40/#45 notes correct, no underscore labels left in ADRs or any GitHub issue. Decision ADRs sound; still blocked on owner: Fly account, Kampala-to-Fly and mobile latency, staging Appwrite project, lawyer/DPO review, audit_log deviation sign-off). Previously: round 2 ready-for-review (branch `d03-hosting-adrs`). Round-1 findings below were resolved 25 Nov 2026:
  1. Labels: all `ops_admin`/`safeguarding_lead`/`super_admin` in ADR 0012 renamed `opsadmin`/`safeguardinglead`/`superadmin` (ADR 0003 had none); principle 4 states the SR §6.1-minus-underscores mapping and that the rule is from the SDK doc comment (users.d.ts), not verified live; re-verify in A03/M05 on staging. GitHub #40 and #45 edited (only issues with underscore labels; note line appended).
  2. Safeguarding scope: removed `label:safeguardinglead` from `child`, `home_address`, `session_log`, `teacher_document`; now `read("user:<S>")` per assigned lead while a case concerns the row, revoked on case close via `permission-reconcile` (new "Safeguarding scope" rule + divergence row). `safeguarding_case`/`case_status` keep the label. `audit_log` keeps blanket label read, marked as a deviation (new owner question).
  3. Substitute window: added to `child`, address, `session_log`, `handover_summary` rows, a rule paragraph and a divergence-table row; `desired_permissions` takes the window as input; revoked by `address-revoke`/`permission-reconcile`.
  4. `read("users")` on published catalogue stated as intentional exception, M05 AC applies to user-data tables; `permission-reconcile`/`permission-audit` noted as to-add to ADR 0002 table in F04. Fly cost wording untouched. `check-adrs.py` 4/4 ok.
- (Round 1 reviewer block, resolved:)
- Reviewer checks: clean worktree, `python3 docs/adr/check-adrs.py` -> 4/4 ok; a bad ADR (bad Status, no Date/Ticket/Decision/Consequences) -> exit 1 with 5 messages, so the lint is meaningful. Latency re-measured 20x on public `/v1/health/version`: connect min/median/p95 87/92/95 ms, TLS 179/184/188, total 429/445/477 (all 200); consistent with the ADR (83 ms median connect; ADR numbers stand, workstation variance). Diff is only the 3 D03 files, no secrets. Cost labelling, capability checklist "verify" marks, DP-7 open item, ledger question, edge cases, blocked-on-owner items: OK. Permission strings match SDK syntax (`Role.label` -> `label:x`, `Role.users()` -> `users`, `Role.user(id)` -> `user:id`).
- **Findings (fix on the branch, then re-submit):**
  1. **Role labels with underscores are invalid.** The installed node-appwrite docs for `users.updateLabels` say labels are "each up to 36 alphanumeric characters" (spikes/admin-appwrite/node_modules/node-appwrite/dist/services/users.d.ts:479). ADR 0012 principle 4 and every table use `ops_admin`, `safeguarding_lead`, `super_admin` (and M05 #40 text uses `safeguarding_lead`). Rename to alphanumeric (e.g. `opsadmin`, `safeguardinglead`, `superadmin`, or camelCase) throughout ADR 0012 and say that the SR §6.1 role names map to labels with underscores removed; note that M05 (#40) and any ticket text must follow. Add a line that this was checked against the SDK doc comment, not the live service.
  2. **Safeguarding lead is given blanket read on `child`, `home_address` and `session_log`**, but SR §6.2 says "Read (cases)" for children and address (only those tied to a case). The ADR presents the table as per-SR-row without flagging the widening. Either scope the lead's access to case-related children/addresses (per-user or per-case grants computed from `safeguarding_case`) or mark it explicitly as a deviation with the reason and a follow-up for the owner to accept.
  3. **Substitute window not covered.** M05 edge case: "Substitute: access only for the covered session window". ADR 0012 only says the teacher is added on confirmation and removed when the assignment ends / substitution replaces a teacher. State when a substitute's `child`, `handover_summary`, `session_log` read is revoked (end of the covered session, via the same reconcile job) and that `desired_permissions` takes the window as input.
  4. Minor, mark in ADR: catalogue `read("users")` for published rows is stated as outside SEC-2/M05 "no any/users" (not user data); say explicitly that M05's AC should be read as applying to user-data tables only. Jobs `permission-reconcile` and `permission-audit` are not in ADR 0002's job list: say "to be added to the ADR 0002 table in F04". Fly `$4` managed Postgres and the shared-cpu price points ($6.70 / $3.69) could not be re-verified by the reviewer; they are already labelled estimates/unverified, keep that wording.
  After these edits the ADRs are otherwise sound; owner-blocked items (Fly account, Kampala-to-Fly and mobile latency, staging Appwrite project, lawyer note) remain acceptable as blocked-on-owner.
- (Previous builder block follows.)
- Built: `docs/adr/0003-core-service-hosting.md`, `docs/adr/0012-appwrite-cloud-and-permissions.md`, `docs/adr/check-adrs.py` (generic ADR lint). Each ADR has options, decision, consequences, cost estimate, unexecuted staging runbook (Fly half in 0003, Appwrite console half in 0012); 0003 has the Fly capability checklist; 0012 has the permission strings per SR 6.2 row, revocation, grant/business-write divergence table, drift check, ledger question, DP-7 lawyer/DPO brief.
- Test: `python3 docs/adr/check-adrs.py` -> 4/4 pass (0002 and 0004 already conformed, no heading edits needed). Negative check: a file missing Date/Ticket/Decision/Consequences exits 1.
- Latency (measured 25 Nov 2026 ~09:30 UTC, workstation IP geolocated to Kampala, AS36924, link type unverified, probably fixed line; 20 fresh-connection requests to the public `GET fra.cloud.appwrite.io/v1/health/version`, no credentials): TCP connect min/median/p95 = 70/83/97 ms; TLS done 144/167/199 ms; full response 393/420/518 ms. Raw table in ADR 0003.
- Cost (estimates, 25 Nov 2026): Fly about $20/month (prod 2x shared-cpu-1x 1GB, staging 1x 512MB, one dedicated IPv4); Appwrite $25 per Pro project (third-party sources, they disagree on per-project vs per-member, official page not fully readable); stack total about $45 (staging on Free) to about $70 (both Pro), +$29 if Fly Standard support. Fly prices are iad/ewr list prices, fra not verified.

| Acceptance criterion | Where / status |
|---|---|
| Both ADRs in docs/adr/ | Done, lint passes |
| Latency Kampala to Appwrite and Fly fra (mobile and fibre) | Appwrite over one Kampala (probably fixed) network: done. Fly `fra`: BLOCKED (no account/app). Mobile networks: BLOCKED (needs phones on MTN/Airtel). Core-to-Appwrite hop: BLOCKED (needs deployed app) |
| Monthly cost estimate | Done, labelled estimate with sources; Appwrite plan terms unconfirmed |
| Fly supports containers, secrets, autoscaling, separate apps | Checklist in ADR 0003; items marked "verify" until a staging deploy. Built-in autoscaling is limited: year one is fixed 2 machines |
| DP-7 note as open item | Lawyer/DPO brief with 9 questions in ADR 0012; owner must engage counsel |
| Ledger open question | In ADR 0012, with recommended default (decide before automated payouts) |
| Edge: latency unacceptable for check-in | Offline queue (Drift) is the mitigation, in ADR 0003 |
| Edge: legal requires in-country hosting | Impact analysis in ADR 0012 (self-hosted Appwrite, region move) |
| Staging from the ADRs alone | Runbooks in both ADRs, NOT executed |

- Deviations: ADR 0003 and 0012 status "Proposed" pending owner sign-off (same as 0002, 0004). Latency to Fly and mobile not measurable by an agent.
- Follow-ups: F02 builds schema from the table split in ADR 0012 and records the diff to the 35 live tables; F03 builds Dockerfile/fly.toml and does the Fly latency run; role-played access tests need a non-live Appwrite project.

### D05 · Email provider decision (#25)
- Status: **blocked-foundation-merged** (PR #159 squash commit a4cc6cb; issue #25 closed). Review: `check-adrs.py` 5/5 ok in clean worktree; diff only ADR 0005, no secrets; Resend pricing/events and Postmark pricing spot-checked against public pages (match); SES/Postmark event names consistent; contract coherent with SR §8 and N02 #127; unverified labels present, nothing claimed tested. Still blocked on owner: domain, Resend account, DNS, inbox test. N02 follow-ups: map/ignore `email.failed` and `email.suppressed`; verify Resend signature header names and idempotency support.
- Built: `docs/adr/0005-email-provider.md` (Proposed): Resend (chosen in SR v0.2) vs Postmark vs Amazon SES, comparison table, `EmailSender` interface contract, webhook event mapping table (delivered, bounced hard/soft, complained) for the three providers, sending-domain DNS plan as a template (`mail.<domain>`, SPF/DKIM/DMARC, p=none then quarantine then reject), owner checklist, deliverability test procedure, Ugandan-hosts mitigation. Prices from public pages fetched 25 Nov 2026, labelled unverified. Event names/signature scheme written from public docs knowledge, marked to verify in N02. No code: adapter is N02 (#127).
- Test: `python3 docs/adr/check-adrs.py` -> 5/5 ok.
- Note: SR section for notifications is §8 (not §9).

| Acceptance criterion / edge case | Evidence |
|---|---|
| ADR 0005 compares deliverability, price, templates, bounce/complaint webhooks | ADR "Options considered" + comparison table (deliverability is qualitative, no measurements) |
| Sending domain chosen | BLOCKED on owner (domain is an owner decision); template plan with placeholders in ADR |
| SPF, DKIM, DMARC plan written | ADR "Sending domain plan" |
| Ugandan mail hosts edge case | ADR test matrix + 5-step mitigation; real test BLOCKED on owner |
| Test email from staging lands in inbox for 3 providers | BLOCKED on owner (domain, Resend account, DNS); procedure in ADR |
| ADR merged / linked from dependent tickets | After review; link from #127 and #44 at merge |

### F01 · Monorepo structure, tooling and contribution rules (#27)
- Status: **changes-requested** (reviewer, 25 Nov 2026; branch `f01-monorepo` commit 537ec1b; no PR opened yet). Blocker #21 (D01) was merged.
- Reviewer evidence (clean worktree): `make lint` exit 0 (layout ok, secret-scan ok, ADR lint 4/4, py_compile); `python3 -m unittest discover -s scripts/tests` Ran 23 OK; `make test` exit 0 (4 ran ok, 6 planned skipped; spikes installed from scratch, jobs-demo 14 passed, admin build ok). Broke it on purpose: `apps/rogue` (unregistered), top-level `newtop/`, README-less registered package -> `make layout` exit 2 with clear message. Planted AWS key, GitHub token, private-key header, `.env` with APPWRITE_API_KEY, `.env.local` with `standard_<64 hex>` in a temp dir: all flagged, value never printed; empty `.env.example`, package-lock/uv.lock hashes, `os.environ[...]`, short token: not flagged. Missing tool: non-strict SKIP exit 0, `HS_STRICT=1` exit 1; `HS_SKIP` works. Mutations (layout registration check off, AWS regex changed, failures not counted) each make a unit test fail, so tests are meaningful. `.gitignore` covers `.env`, `.env.*` (so `.env.local`), keeps `.env.example`; only `spikes/admin-appwrite/.env.example` tracked. Relative links in README/CONTRIBUTING/CLAUDE/package READMEs all resolve. Diff is only F01 (24 files), no secrets. CLAUDE.md/CONTRIBUTING rules match Owner decisions and ADRs 0002/0003/0004/0012 (alphanumeric labels, 5,000 cap, no conditional update/triggers/exclusion, read-only Appwrite, memory.md on main).
- **Findings (fix on the branch, re-submit):**
  1. **Pre-commit hooks fail on this repo's own files** (acceptance criterion "hooks for formatting and linting in each language"). In `.pre-commit-config.yaml` the `ruff-check` and `ruff-format` hooks use `files: \.py$` (named "services/core" but unscoped) so they run on `scripts/*.py` and `spikes/jobs-demo`. Reviewer ran `uvx ruff check scripts` -> 6 errors and `uvx ruff format --check scripts` -> 4 of 5 files would be reformatted, so any commit touching those files would be blocked once `make hooks` is installed. Fix: scope hooks to the package they name (`^services/core/.*\.py$`; dart hooks to `^(apps|packages)/.*\.dart$`), or ruff-format the scripts and fix the 6 lint errors; and note hooks were not executed (pre-commit missing).
  2. Secret scan misses JSON/quoted keys: `{"password": "Zq9xLm2PwRt7YuIo4Ks81"}` is not flagged (the `generic-assignment` regex needs `key[:=]` with no closing quote between). Allow an optional quote before the separator and add a unit test. Also the placeholder filter skips any line containing `your|example|<|process.env...` anywhere, so `secret = "<20+ chars>" # example` passes; narrow it to the value (not the whole line) or document it as a known limit in `scripts/secret_scan.py`.
  3. Minor doc: CONTRIBUTING step 7 says to open a PR; CLAUDE.md rule 9 says builders do not open PRs (reviewer does). Say "humans open the PR; agent builders hand off to the reviewer".
  Not blocking: red-before-green not captured (mutations show tests fail when code is broken); branch protection owner-blocked until F07 CI exists (acceptable); no CI (F07).
- (Builder block follows.) Partly blocked on owner: branch protection (see below).
- Built: layout dirs each with README (`## Purpose`, `## Test command`): apps/parent, apps/teacher, apps/admin, packages/flutter_shared, services/core, appwrite, docs (+`docs/product` placeholder), scripts. `packages.json` manifest (implemented vs planned + ticket, requires, test, lint); `scripts/run_packages.py` (runs manifest commands; planned -> `SKIP ... not implemented yet (planned for F0x)`; missing tool -> `SKIP ... tool missing`; `HS_STRICT=1` makes that fail); `scripts/check_layout.py` (layout guard); `scripts/secret_scan.py` (+ `.gitleaks.toml`); `Makefile` (test, lint, layout, secrets, hooks, tools, help); `.editorconfig`; `.pre-commit-config.yaml` (dart format, ruff, prettier, eslint, gitleaks, make lint); `CLAUDE.md`, `CONTRIBUTING.md`; `.github/CODEOWNERS` (`* @Matt-Murungi`), `.github/pull_request_template.md` (Closes #, 🤖 line); root README layout. `spikes/` untouched; both spikes are in the manifest as implemented (admin spike's `make test` does `npm ci` + tsc + lint + build, needs network).
- Tests: `scripts/tests/test_layout.py` (23 tests). Written before the code; I did not capture a separate red run. Result on branch: `python3 -m unittest discover -s scripts/tests` OK 23; `make lint` OK; full `make test` exit 0 (4 ran: docs ADR lint, root-tests, both spikes; 6 planned skipped with messages), in a fresh worktree (spikes installed from scratch).
- Tools on this machine: flutter, dart, node, npm, uv, make, python3 present; pre-commit and gitleaks missing (not installed; hooks config is untested here, `make lint` covers the same checks).

| Criterion / edge case | Evidence |
|---|---|
| Folder layout with README per package | `Layout.test_directories_exist_with_readme`, `test_docs_subfolders`, `test_manifest_covers_every_package`, `test_root_readme_lists_layout` |
| README states purpose and test command | `test_each_readme_states_purpose_and_test_command` |
| CLAUDE.md: stack, commands, branch/PR rules, positioning copy rule, agent rules (tests-first, adapters/fakes, secrets, read-only, UGX, UTC/Kampala, ADR caps, memory.md) | `Docs.test_claude_md`, `test_contributing` (checks each rule phrase present) |
| Pre-commit hooks per language | `test_precommit_has_each_language` (config content only; hooks not executed, pre-commit not installed) |
| Branch protection on main | OWNER-BLOCKED, see below |
| Edge: package outside layout -> lint fails clearly | `LayoutGuard.test_unknown_top_level_dir_fails`, `test_unregistered_package_under_apps_fails`, `test_missing_readme_fails`, `test_repo_passes` (`make lint` runs the guard) |
| Fresh clone runs every package's test command | `Tooling.test_make_test_and_lint_exit_zero`; planned packages skip honestly (`test_planned_packages_skip_clearly`); missing tools degrade (`test_missing_tool_degrades_with_message`); failure propagates (`test_failing_package_fails_run`); full run done by hand |
| Secret scan flags planted secret, repo clean, never echoes the value | `SecretScan.test_flags_planted_secrets`, `test_clean_dir_and_placeholders_pass`, `test_repo_is_clean` |
| CODEOWNERS, PR template/issue-link | `test_codeowners_owner`, `test_pr_template` |
| CI green / staging deploy | Out of scope here: CI is F07 (#33). Make F07 run `HS_STRICT=1 make test && make lint`. |

- Deviations: no CI workflow (F07). Planned packages have no placeholder test (honest skip instead). `gitleaks`/`pre-commit` not run locally.
- Follow-ups: each scaffolding ticket (F02 appwrite, F03 core, F05 apps/shared, F06 admin) flips its `packages.json` status to `implemented` and adds its row to "How to run tests"; add a lint command for the spikes only if wanted.

## Blocked on owner (credentials / deployments / accounts)

- D02: staging demo of the job tick (needs deployed core service + non-live Appwrite project).
- D03: Fly.io account/org and billing; throwaway Fly app to measure Kampala to `fra`; mobile-network latency (MTN, Airtel) to Appwrite and Fly; a non-live Appwrite project (`hs-staging`, Frankfurt) with a scoped API key; Appwrite plan/cost confirmation; lawyer/DPO review of the DP-7 note (ADR 0012).
- D05: sending domain; Resend account (Free ok for staging); DNS access to add SPF/DKIM/MX/DMARC records for `mail.<domain>`; Resend API key + webhook secret into staging Fly secrets; DPA signed (DP-9); then the inbox test (Gmail, Yahoo, a Ugandan host). Exact steps: owner checklist in ADR 0005.
- F01: GitHub branch protection on `main` (Settings > Branches > rule for `main`): require a pull request before merging (1 approval can stay 0 because the reviewer agent posts comment reviews under the owner's account; enable "Dismiss stale approvals" off), require status checks to pass and branch up to date, the checks being the F07 (#33) job names (suggest `lint` and `test`, running `make lint` and `HS_STRICT=1 make test`), restrict force-push and deletion, and allow squash merge only. Optionally a CODEOWNERS review requirement and a GitHub team in `.github/CODEOWNERS` (currently `@Matt-Murungi`). Can only be applied once F07 CI exists.

## Questions for the owner (collected; ask at the end)

1. Where does the existing 35-table Appwrite schema come from? (Diff against the fresh schema will be added when M01–M04 are done.)
2. Sign off ADRs 0002 and 0004.
3. Appwrite plan: one database only; function execution allowance for a once-a-minute tick?
4. Security: `general_variables.md` in iCloud holds a plaintext GitHub token — revoke/rotate it.
5. SR cites ADR 0013 (reduced pilot scope, 18 Jan 2027); no ticket and no ADR exist for it.
6. (D03) Sign off ADRs 0003 and 0012. Default: accept.
7. (D03) Appwrite plan: is Pro $25 per project or per member; do we need Pro for staging? Does the plan give point-in-time recovery (NFR-6)? Default: Pro for prod and staging; ask Appwrite in writing; if no PITR, add a nightly export job.
8. (D03) Engage a Ugandan lawyer/DPO on the DP-7 brief (9 questions in ADR 0012). Default: before the pilot if real children's data is used, otherwise before public launch. If in-country hosting is required, ADRs 0003/0012 are revisited (self-hosted Appwrite).
9. (D03) Create a Fly.io account and a throwaway app, and run the latency script from Kampala on fibre and on MTN and Airtel phones. Default: do it with the F03 staging deploy.
10. (D03) Ledger in Appwrite or Postgres (SR 14 item 2)? Default: keep behind a `LedgerStore` interface on Appwrite for the pilot, decide before automated payouts after a crash-between-postings spike (leaning Postgres).
11. (D03) Fly Standard support ($29/month)? Default: skip for the pilot.
12. (D03) Accept safeguarding lead's blanket read of `audit_log` (deviates from SR §6.2 "Read (cases)")? Default: accept for the pilot, or scope to case-related entries.
13. (D05) Sign off ADR 0005 (Resend). Default: accept.
14. (D05) Which domain sends mail? Default: `mail.<main domain>` subdomain, from `no-reply@` and `safeguarding@`, reply-to `support@<main domain>`. DNS checklist: add Resend's SPF TXT, bounce MX and DKIM records on `mail.<domain>`, then DMARC `v=DMARC1; p=none; rua=mailto:dmarc@<domain>` on `_dmarc.mail.<domain>` (tighten to quarantine/reject after 2-4 weeks of reports); verify in Resend.
15. (D05) Run the inbox test from staging to Gmail, Yahoo and a Ugandan ISP/company address and send the headers (spf/dkim/dmarc pass). Default: fall back to Postmark if any fail.

## Session log

- 2026-11-25: D01 merged (PR #156). D02 merged (PR #157) after round 2.
- 2026-11-25: Repo bootstrapped; backlog (135 tickets) imported and updated for Appwrite; D01, D02 built; two-agent workflow agreed.
- 2026-11-25: D03 built (ADRs 0003, 0012, ADR lint), ready-for-review on `d03-hosting-adrs`.
- 2026-11-25: D03 round 2 reviewed, PR #158 merged, status blocked-foundation-merged.
- 2026-11-25: D05 built (ADR 0005), ready-for-review on `d05-email-adr`.
- 2026-11-25: D05 reviewed, PR #159 merged, status blocked-foundation-merged.
- 2026-11-25: F01 built, ready-for-review on `f01-monorepo`.
- 2026-11-25: F01 reviewed, changes-requested (3 findings).
