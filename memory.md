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
- Status: **changes-requested** (branch `d02-job-runner`; reviewer 2026-11-25)
- Review commands: `cd spikes/jobs-demo && uv run pytest -q` -> 11 passed; concurrency test x20 -> 20/20 passed (not flaky, but see finding 3). Diff has no secrets (uv.lock hashes only). Branch is behind main only by D01/memory commits, no conflict expected.
- Criteria: ADR compares options on cost/ops/retries/cron/exactly-once OK; auth (HMAC header, 5 min skew, 25 s timeout/20 s budget) OK; retries and catch-up location OK; staging hello-world honestly marked not done and listed under Blocked on owner OK (accepted for a decision ADR + tested demo). Constant-time compare, body binding, timestamp skew, backoff arithmetic (base*2^(attempts-1)), catch-up arithmetic, chunk/cursor, lease expiry on final attempt (verified by repro: goes to failed + alert): all correct.
- FINDINGS (must fix):
  1. **BUG, run can be stranded forever (violates "nothing is lost").** `Runner._claim` creates the `job_attempt` row, then updates `job_run`. If the worker dies/times out/hits an Appwrite error between those two calls, the run stays `queued` with `attempts` unchanged; every later tick computes the same attempt ID, gets Conflict, counts it as `lost_claims`, and the run is never executed or failed. Reproduced: attempt row inserted for (run, chunk 0, attempt 1) with run still queued, ticks at +0/+10/+600 min -> handler never called, status stays queued. Fix suggestion: on Conflict, read the existing job_attempt and, if its `started_at` is older than LEASE and job_run is still queued/attempts==attempt-1, treat it as an orphaned claim (bump attempts / move to next attempt ID, or mark the run running-with-expired-lease so the existing recovery path takes over). Same hole applies to the Continue path (cursor update fails after handler ran: re-run of same chunk is fine since effects are idempotent). Add a test that simulates the crash between the two writes (the existing crash test only covers crash after both writes) and update ADR rule 2/5 to describe the recovery.
  2. ADR "Evidence" says "All 11 tests pass" but the table lists 10; and no test covers lease expiry on the final attempt (code handles it; add a test: max_attempts=1, claim, tick after lease -> failed + alert, handler not re-run). ADR edge case "crash recovery" should name this.
  3. Concurrency test is weak (handlers finish in microseconds, so threads may mostly serialise under the GIL). Strengthen: add a barrier and a handler that sleeps briefly, or inject a store that yields between list and claim, and also assert `lost_claims` > 0 in total.
  4. ADR claims the live `job_run`/`job_attempt` columns and the `by_status_run_at` index exist; this could not be verified (live project is read-only, no API calls). State in the ADR that these come from the owner's earlier inspection and must be re-checked in F04, or drop the claim.
  5. Minor / follow-up (not blocking): `materialise` makes one create call per period in the window on every tick (a 1-minute job with 2-day catch-up = 2,880 calls/tick, each a 409 after the first); record a "last materialised" watermark or bound the window per job in F04. `compare_digest` raises TypeError (500) on a non-ASCII signature header; encode to bytes or catch. `runner.tick()` is sync inside an async FastAPI handler (blocks the loop); use a plain `def` endpoint. `_claimable` lists all job_run rows (5,000 cap, ADR 0004); ADR already notes the index follow-up.
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
