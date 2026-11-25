# Admin dashboard spike (D01 / ADR 0004)

This is a throwaway spike. It proves that Next.js and Appwrite can handle the admin dashboard's core pattern: a large table with server-side pagination, and a validated form that writes to Appwrite. The real admin app is built in F06. Don't extend this one.

## What it shows

- `/sessions`: 50 rows per page from a 10,000-row Appwrite table, with page navigation, a status filter and the query time displayed.
- `/sessions/new`: a server-validated form. Field errors stay on the form, and a valid submit creates a row and returns to the list.

## Run it

1. In the **Development** Appwrite project, create an API key with database, table, column, index and row read/write scopes.
2. Set up and seed:

   ```sh
   cp .env.example .env.local   # fill in project ID and API key
   npm install
   npm run seed                 # creates database/table/indexes, fills to 10,000 rows (re-runnable)
   ```

3. Build, start and time it:

   ```sh
   npm run build && npm start
   npm run timing               # in another terminal; exits non-zero if any page takes 2 s or more
   ```

`npm run timing` loads the first, middle and deepest pages, plus filtered pages, five times each. It reports the median and worst full response times.

4. Remove the spike data: `npm run cleanup`. This deletes only the spike's sessions table.

If your Appwrite plan allows only one database, set `APPWRITE_DATABASE_ID` to the existing database and `APPWRITE_SESSIONS_TABLE_ID=zz_spike_sessions`. The seed then adds just that one table, with row security on and no client permissions. Results are recorded in ADR 0004.

## Shortcuts this spike takes on purpose

- It uses a server API key instead of the signed-in admin's session. The real app must not (see ADR 0004 › Consequences).
- It has no auth, roles, MFA or audit logging.
