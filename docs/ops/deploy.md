# Deploy, promotion and rollback (F07, #33)

Workflows: `.github/workflows/deploy-staging.yml` (automatic on merge to `main`), `promote-production.yml`
(manual). Both are inert until the owner sets the repository variable `DEPLOY_ENABLED=true`
(see [ci.md](ci.md) for the owner checklist). Agents never deploy.

## Order of operations (always)

1. **Appwrite schema first**: `python3 appwrite/migrate.py --env <staging|production>` (F02 runner).
2. **Core service second**: `flyctl deploy --config fly.<env>.toml --app $FLY_APP --remote-only` from `services/core`.
3. **job-tick function** (needs the core URL): `appwrite push functions --function-id job-tick`.
4. **Admin app**: built, deploy step is a TBD (below).

Steps 2-4 run only if step 1 exited 0 **and** the runner reported `promote=true` (the `promote` step/job
output). A migration that fails halfway therefore stops the pipeline: the API and functions are not touched,
the runner has already recorded the scripts that did finish, and the failure is retried (pending scripts only)
by re-running the workflow after the fix. Staging is never "half deployed": the old API keeps serving a schema
that was only expanded (rule below).

Two schema runs never overlap: the schema job has the concurrency group `appwrite-schema-<env>` with
`cancel-in-progress: false`; the workflows have their own group so a second push queues behind the first
(a deploy is never cancelled mid-flight).

## Schema compatibility rule: expand, then contract

Every migration must be **backward compatible with the previous API release for one release**, because the
schema is applied before the API and the rollback target is the previous API.

- **Expand** (release N): add tables, add nullable/optional columns, add indexes. Never rename, retype or
  drop in the same release as the code that stops using the old shape.
- **Migrate**: ship API N that writes the new shape and still reads the old one; backfill by a migration or job.
- **Contract** (release N+1 or later, after N is stable and rollback to N-1 is no longer wanted): drop the
  old column/table in its own migration.
- Never edit an applied migration (the runner rejects it); add a new numbered script.
- Seeds never run in production (the runner refuses with exit 3).

## Promotion to production

Actions tab, workflow `promote-production`, Run workflow on `main`, input: the 40-character commit sha that is
healthy on staging. The job verifies the sha is an ancestor of `origin/main`, then waits for the required reviewer
of the `production` environment. Same order as above in one job. Production rebuilds the image from the same
sha (reproducible Dockerfile); switching to promoting the staging image is a possible refinement.

## Rollback plan

| Layer | Rollback |
|---|---|
| Core API | Redeploy the previous image. `fly releases --app $FLY_APP` lists releases and images; `flyctl deploy --image <previous image ref> --config fly.<env>.toml --app $FLY_APP` (or re-run `promote-production` with the previous good sha). Fly's rolling deploy with health checks already keeps the old release serving when the new one fails its checks. |
| Schema | Not rolled back. Because of the expand/contract rule the previous API works on the new schema. If a migration itself is wrong, fix forward with a new numbered migration (never delete rows or drop columns by hand). |
| job-tick function | Redeploy the previous sha with `appwrite push functions` (re-run the workflow with the previous sha); the core API stays authoritative, so a missed tick only delays jobs (ADR 0002). |
| Admin app | Redeploy the previous build; stateless. |

Decision order when production is unhealthy: (1) roll the API back (minutes, safe because schema is compatible),
(2) investigate, (3) fix forward. A schema that cannot be used by the previous API violates the rule above and
is a review blocker.

## Staging health check

`curl --fail "$STAGING_CORE_URL/health"` runs at the end of the core step (retries 6x10s). Target from #33:
a merge reaches staging within 15 minutes (remote build is the long part, typically 3-6 minutes; not measured,
owner-blocked until DEPLOY_ENABLED).

## Admin host (TBD)

ADR 0004 chose Next.js but names no host. The workflows build the app and print a notice; once the owner picks a
host, replace the step "Deploy the admin app (TBD)" with its command and add the token as an environment secret.

## Unverified (never run, no accounts)

`flyctl deploy` flags, `fly.staging.toml` / `fly.production.toml`, the Appwrite CLI invocation
(`appwrite-cli@28.16.0 client ... / push functions`), the REST adapter behind `migrate.py` (F02 note), and the
workflows themselves until the first CI run on the PR. Verify on staging first.
