// Creates the spike database and `sessions` table in Appwrite and fills it to 10,000 rows.
// Safe to re-run: existing database, table, columns and rows are kept.
// Usage: node --env-file=.env.local scripts/seed.mjs [target=10000]
import { Client, ID, TablesDB, TablesDBIndexType, AppwriteException } from "node-appwrite";

const TARGET = Number(process.argv[2] ?? 10_000);
const DB = process.env.APPWRITE_DATABASE_ID ?? "admin_spike";
const TABLE = process.env.APPWRITE_SESSIONS_TABLE_ID ?? "sessions";
const SUBJECTS = ["English", "Mathematics", "Science", "Social Studies", "Literacy"];
const STATUSES = ["scheduled", "completed", "confirmed", "disputed", "cancelled"];
const FIRST = ["Amani", "Brian", "Grace", "Joan", "Ivan", "Sarah", "Peter", "Esther", "Daniel", "Ruth", "Moses", "Faith"];
const LAST = ["Okello", "Nakato", "Mugisha", "Achieng", "Ssempala", "Namubiru", "Kato", "Atim", "Byaruhanga", "Nabirye"];

for (const k of ["APPWRITE_ENDPOINT", "APPWRITE_PROJECT_ID", "APPWRITE_API_KEY"]) {
  if (!process.env[k]) throw new Error(`Missing ${k}; copy .env.example to .env.local`);
}
const db = new TablesDB(
  new Client()
    .setEndpoint(process.env.APPWRITE_ENDPOINT)
    .setProject(process.env.APPWRITE_PROJECT_ID)
    .setKey(process.env.APPWRITE_API_KEY),
);

async function ignoreExists(fn) {
  try {
    return await fn();
  } catch (e) {
    if (e instanceof AppwriteException && e.code === 409) return null;
    throw e;
  }
}

// Create the database only if it is missing (some plans allow a single database).
try {
  await db.get({ databaseId: DB });
} catch (e) {
  if (!(e instanceof AppwriteException && e.code === 404)) throw e;
  await db.create({ databaseId: DB, name: "Admin spike (D01)" });
}
await ignoreExists(() => db.createTable({ databaseId: DB, tableId: TABLE, name: "ZZ spike sessions (D01, delete after)", rowSecurity: true }));

const col = { databaseId: DB, tableId: TABLE };
await ignoreExists(() => db.createStringColumn({ ...col, key: "child_name", size: 64, required: true }));
await ignoreExists(() => db.createStringColumn({ ...col, key: "teacher_name", size: 64, required: true }));
await ignoreExists(() => db.createEnumColumn({ ...col, key: "subject", elements: SUBJECTS, required: true }));
await ignoreExists(() => db.createEnumColumn({ ...col, key: "status", elements: STATUSES, required: true }));
await ignoreExists(() => db.createDatetimeColumn({ ...col, key: "scheduled_at", required: true }));
await ignoreExists(() => db.createIntegerColumn({ ...col, key: "amount_ugx", required: true, min: 0, max: 10_000_000 }));

async function waitFor(kind, list) {
  for (let i = 0; i < 60; i++) {
    const { [kind]: items } = await list();
    const pending = items.filter((c) => c.status !== "available");
    if (pending.some((c) => c.status === "failed")) throw new Error(`${kind} failed: ${JSON.stringify(pending)}`);
    if (pending.length === 0) return;
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`${kind} still processing after 60 s`);
}
await waitFor("columns", () => db.listColumns(col));

await ignoreExists(() => db.createIndex({ ...col, key: "idx_scheduled", type: TablesDBIndexType.Key, columns: ["scheduled_at"] }));
await ignoreExists(() =>
  db.createIndex({ ...col, key: "idx_status_scheduled", type: TablesDBIndexType.Key, columns: ["status", "scheduled_at"] }),
);
await waitFor("indexes", () => db.listIndexes(col));

const { total } = await db.listRows({ ...col, queries: [] });
console.log(`table has ${total} rows; target ${TARGET}`);

const pick = (a) => a[Math.floor(Math.random() * a.length)];
const start = Date.UTC(2027, 0, 18);
let made = total;
while (made < TARGET) {
  const n = Math.min(100, TARGET - made);
  const rows = Array.from({ length: n }, () => ({
    $id: ID.unique(),
    child_name: `${pick(FIRST)} ${pick(LAST)}`,
    teacher_name: `${pick(FIRST)} ${pick(LAST)}`,
    subject: pick(SUBJECTS),
    status: pick(STATUSES),
    scheduled_at: new Date(start + Math.floor(Math.random() * 365) * 86_400_000 + (13 + Math.floor(Math.random() * 6)) * 3_600_000).toISOString(),
    amount_ugx: pick([30_000, 35_000, 40_000, 45_000, 50_000]),
  }));
  await db.createRows({ ...col, rows });
  made += n;
  if (made % 1000 === 0 || made === TARGET) console.log(`  ${made} rows`);
}
console.log("seed complete");
