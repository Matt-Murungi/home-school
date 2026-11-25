# Roles and app routing (A03, #45)

Roles are Appwrite **user labels**, alphanumeric only (ADR 0012): `parent`, `guardian`, `teacher`,
`opsadmin`, `curator`, `safeguardinglead`, `superadmin`. Matching is exact and case-sensitive:
`ops_admin`, `OpsAdmin`, ` opsadmin` and unknown labels grant nothing, in the core service, the
admin app and the mobile apps. The single source is `hs_core.roles.model`; the shared fixture
`docs/ops/roles-matrix.json` is read by the Python tests, the admin tests
(`apps/admin/tests/roles-fixture.test.ts`) and the Flutter tests (`packages/flutter_shared/test/roles_test.dart`),
so none of the three can drift without a failing test.

## What each client admits

| Client | Admits | Notes |
|---|---|---|
| Parent app | `parent`, `guardian` | a mixed parent + teacher user is admitted to both apps |
| Teacher app | `teacher` | approval and data access come later (VER-1, ADR 0012 per-user grants) |
| Admin web | `opsadmin`, `curator`, `safeguardinglead`, `superadmin` | MFA required (SEC-4); A04 owns the login flow, this ticket only the guard contract (`admin_admits`, `GET /v1/me/access?app=admin` returns `mfa_required`) |

`superadmin` is not implicit: it opens Overview and Settings only (SR 6.2 has no super admin
column); a super admin who needs payments also holds `opsadmin`. Route table: `ADMIN_ROUTES`.

Pure functions (`hs_core/roles/model.py`): `clean_labels`, `roles_for(user)` (empty for a suspended,
deactivated or soft-deleted user), `app_admits(app, roles)`, `admin_admits(route, roles)`
(longest-boundary match, unknown routes denied), `app_decision(app, labels, status)` (`admit`,
`wrong_app` with the app to use, `suspended`, `no_role`).

## Where roles come from (never from the client)

Core guards (`require_roles`) read labels from the **verified Appwrite account** (`GET /account`
with the caller's JWT, fresh on every request, so a label change applies at once). Headers, query
strings and bodies carrying `roles`/`labels` are ignored (tests). `GET /v1/me/access?app=` answers
what the caller may do in an app from the same verified labels plus the stored account status
(`user_profile.status`, and `teacher_profile.status` for the teacher app).

## Changing a role: `RoleService.grant_role / revoke_role` (the only writer)

Who may do what (defaults; owner question 36):

| Change | Allowed |
|---|---|
| grant/revoke `opsadmin`, `curator`, `safeguardinglead`, `superadmin` | a `superadmin` only; never yourself; system actors never grant staff roles |
| grant/revoke `parent`, `guardian`, `teacher` | `opsadmin` or `superadmin`; the system (sign-up grants `parent`/`teacher`, the guardian link grants `guardian`); a user may drop their own mobile role |
| the last `superadmin` | cannot be revoked (checked before, and again after, because two revokers can race) |
| target suspended, deactivated, soft-deleted, blocked or unknown | grant refused (`409 target_not_active`/`404`); revoke allowed |
| a role staff withdrew (`revoke_reason` starts `staff:`) | not given back by signing in again (`role_revoked`); staff can grant it deliberately |

Order (ADR 0012): grant and non-staff revoke are **audit intent + history row (one audited write), label, `role_changed` event**. A STAFF revoke goes the other way round: **audit intent, label removed, the user's Appwrite sessions ended (`UserDirectory.end_sessions`, best effort), history row closed, event**, because core guards read the label live and removal is what ends admin access (never a closed row with the label still set). Non-staff revokes stay row-first: label-first would let reconcile re-grant the label. A staff GRANT stays row-first, so a crash gives the user less access than recorded. `end_sessions` over Appwrite (`users.delete_sessions`) is mock-tested only; verifying it is part of the owner's throwaway-project run. Both stores lack
transactions, so the failure modes are fixed:

| Crash after | State | Repair |
|---|---|---|
| audit intent only | nothing changed | retry |
| grant: row written, label missing | user holds less than recorded (safe) | the same call again finishes it; `role-reconcile` completes non-staff grants and closes staff ones |
| non-staff revoke: row closed, label still set | over-grant window (user: rows only) | the same call again removes the label; `role-reconcile` removes it |
| STAFF revoke: label removed (and sessions ended), row still open | access already gone; row says granted | the same call again closes the row; `role-reconcile` closes a staff row that has no label |
| label set, no row (A01 older flow, console) | label without history | non-staff: history row written; staff: reported only (owner-set bootstrap label) |

Uniqueness: Appwrite has no conditional update, so the active grant is a claim row whose id is
derived from (user, role, generation); a 409 means another writer took that generation (same idiom
as ADR 0002). One process serialises changes per user with a lock; across processes the claim row
and the reconcile job keep row and label together. Every change is idempotent: a repeated grant or
revoke is a no-op with no second row, intent or event. Each change emits the M05 `role_changed`
event (versioned id, so a repeat is not deduplicated away) and is audited through
the M06 `AuditedRepository` (one audit row per change, audit-first; staff roles require a reason; see `docs/ops/audit.md` "Role changes").

HTTP: `POST /v1/admin/roles/{user_id}/grant|revoke` (body `{role, reason}`, nothing else: extra
fields are 422). The actor is the verified caller. The admin screen is A-51.

## `role-reconcile` (F04 schedule, hourly)

Repairs drift per the table above; rows younger than `ROLE_RECONCILE_GRACE_S` (default 300) are
left alone. Staff labels without a row are only reported (alert `role_drift`) unless
`ROLE_RECONCILE_REMOVE_UNRECORDED_STAFF=true`. Chunked by (phase, cursor), idempotent.

## Phone sign-in (A01) and staff (owner question 27, default implemented)

A user holding any staff label gets **no session** from the phone flow: `403 staff_account` after
the code is proven (so it cannot be used to probe which numbers are staff), no token, no label, no
profile, no role row. It is a clear error on purpose (staff accounts are internal). A suspended
user, or a suspended teacher in the teacher app, gets `403 account_suspended` (a state to explain,
not a login error); a role staff withdrew gets `403 role_revoked`.

## Flutter routing (`packages/flutter_shared`)

`buildAppRouter` guards every route except welcome, sign-in, splash and `/suspended` (deny by
default). Loading: only the splash is built (no protected content flashes). Signed out: protected
routes go to the phone screen and the deep link is remembered (`from`, validated: plain in-app
protected paths only). Wrong role: only the "wrong app" screen (names the app to use; staff are
pointed to the web dashboard) with sign out. Suspended: only the suspended screen. A resume re-reads
labels and status (`SessionRefresher` -> `SessionController.refresh`), so a role change re-routes
without signing in again. An offline start uses the labels cached with the session.

Hooks left for other tickets: `buildAppRouter(landingPath:)` (V01 sends every restored teacher to
`/apply`; V03 owns gating that by application status and plugs in here), `accessSourceProvider`
(V03: a core-backed `AccessSource` for a suspension that happens while signed in), `tabBuilder`.

## Unverified until a throwaway Appwrite project exists (owner-blocked)

1. `users.list` filtered by `labels` (`Query.equal("labels", [role])`) and cursor paging by `$id`
   (the reconcile job's label scan); 2. `users.update_labels` accepts exactly the seven labels and
   rejects underscores; 3. `account.get()` returns the labels on a fresh session; 4. wiring the
   real label directory behind M05's `LabelDirectory` (staging blocker, see memory.md).

## Limits worth knowing

- **Multi-instance.** The per-user lock is in process, and `AppwriteUserDirectory.add_label` / `remove_label` read-modify-write the whole label list, so two instances changing one user's labels within milliseconds can lose an update (for example resurrect a just-revoked label). The claim row keeps `role_assignment` right and `role-reconcile` heals the labels (revoked row + label -> remove; row + no label -> repair). Acceptable for one Fly machine; revisit when scaling out.
- **Last superadmin.** The check counts active `role_assignment` rows only: an owner-bootstrapped label-only superadmin is not counted, so a row-based superadmin cannot be revoked "by" that bootstrap admin (`last_superadmin`). Conservative: it never over-grants.
- **Rate limit.** `/v1/admin/roles/*` allows `ROLE_CHANGES_PER_HOUR` (default 60) grants plus revokes per actor per instance; above it 429 with `Retry-After`.
- **Reconcile isolation.** A malformed `role_assignment` row (missing or wrong-typed `user_id`, missing or unparseable `granted_at`, oversized values) is counted (`malformed`), logged by table and row id only, alerted (`role_malformed_row`) and skipped: the cursor advances and the rest of the page is repaired. Outages (repository or directory) still abort the chunk so F04 retries it.
- **Sign-out.** A session refresh that started before sign-out cannot re-save the session secret or cached labels (generation counter).
