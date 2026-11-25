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

## Blocked on owner (credentials / deployments / accounts)

- D02: staging demo of the job tick (needs deployed core service + non-live Appwrite project).

## Questions for the owner (collected; ask at the end)

1. Where does the existing 35-table Appwrite schema come from? (Diff against the fresh schema will be added when M01–M04 are done.)
2. Sign off ADRs 0002 and 0004.
3. Appwrite plan: one database only; function execution allowance for a once-a-minute tick?
4. Security: `general_variables.md` in iCloud holds a plaintext GitHub token — revoke/rotate it.
5. SR cites ADR 0013 (reduced pilot scope, 18 Jan 2027); no ticket and no ADR exist for it.

## Session log

- 2026-11-25: D01 merged (PR #156). D02 merged (PR #157) after round 2.
- 2026-11-25: Repo bootstrapped; backlog (135 tickets) imported and updated for Appwrite; D01, D02 built; two-agent workflow agreed.
