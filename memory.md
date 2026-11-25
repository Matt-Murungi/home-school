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

Each ticket has a block under "Ticket log" with a `Status`:
`todo` → `in-progress` (main) → `ready-for-review` (main) → `changes-requested` (reviewer) → `ready-for-review` … → `merged` (reviewer) | `blocked-foundation-merged`.
Main agent writes: what was built, files, test command + result, which acceptance criteria/edge cases map to which tests, deviations, open questions.
Reviewer writes: commands run, results, criterion-by-criterion verdict, findings, PR number, merge commit.

## How to run tests

| Package | Command |
|---|---|
| spikes/admin-appwrite | `cd spikes/admin-appwrite && npm ci && npx tsc --noEmit && npm run lint && npm run build` |
| spikes/jobs-demo | `cd spikes/jobs-demo && uv run pytest -q` |

(Add a row for each new package.)

## Ticket queue (MVP build order)

Source of truth for order and dependencies is each issue. Epics E00–E19, 135 tickets (issues #21–#155; epics #1–#20).
Suggested order: E00 decisions (D01–D03, D05; D04, D06 later/owner) → E01 foundations → E02 data model → E03 auth → E04 catalogue → E05 teacher onboarding → E06 parent onboarding → E07 plans → E08 payments → E09 matching → E10 scheduling → E11 session day → E12 logs → E13 safeguarding → E14 pay → E15 notifications → E16 admin → E17 compliance → E18 assessments → E19 quality/launch. Phase 2/3 tickets last.

## Ticket log

### D01 · Admin dashboard framework (#21)
- Status: **ready-for-review** (branch `d01-admin-framework`)
- Built: ADR `docs/adr/0004-admin-dashboard-framework.md`; spike `spikes/admin-appwrite/` (paginated table + validated form, seed/timing/cleanup scripts).
- Evidence: 10,000 rows seeded into a temporary table in the live project's `main` database, timed (median 314–350 ms, worst 984 ms, every page <2 s), then the temp table was deleted; the 35 original tables untouched. **This was done before the read-only rule** — do not repeat it.
- Finding: Appwrite caps list `total` at 5,000 (recorded in the ADR).
- Open: owner sign-off on the ADR (status "Proposed"); spike's table-timing step needs a throwaway project to re-run.

### D02 · Job runner (#22)
- Status: **ready-for-review** (branch `d02-job-runner`)
- Built: ADR `docs/adr/0002-job-runner.md`; demo `spikes/jobs-demo/` (runner, signed tick endpoint, Appwrite Function, 11 tests, all passing).
- Open: "hello-world job on staging" needs F03 (#29) + F02 (#28) and a non-live Appwrite project → **Blocked on owner**. Owner sign-off on ADR. Check Appwrite plan allows ~43,000 function runs/month.

## Blocked on owner (credentials / deployments / accounts)

- D02: staging demo of the job tick (needs deployed core service + non-live Appwrite project).

## Questions for the owner (collected; ask at the end)

1. Where does the existing 35-table Appwrite schema come from? (Diff against the fresh schema will be added when M01–M04 are done.)
2. Sign off ADRs 0002 and 0004.
3. Appwrite plan: one database only; function execution allowance for a once-a-minute tick?
4. Security: `general_variables.md` in iCloud holds a plaintext GitHub token — revoke/rotate it.
5. SR cites ADR 0013 (reduced pilot scope, 18 Jan 2027); no ticket and no ADR exist for it.

## Session log

- 2026-11-25: Repo bootstrapped; backlog (135 tickets) imported and updated for Appwrite; D01, D02 built; two-agent workflow agreed.
