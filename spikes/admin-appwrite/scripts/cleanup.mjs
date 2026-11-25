// Deletes the spike's sessions table (and all its rows). Touches nothing else.
// Usage: node --env-file=.env.local scripts/cleanup.mjs
import { Client, TablesDB } from "node-appwrite";

const DB = process.env.APPWRITE_DATABASE_ID ?? "admin_spike";
const TABLE = process.env.APPWRITE_SESSIONS_TABLE_ID ?? "sessions";
if (!/spike/.test(TABLE) && !/spike/.test(DB)) throw new Error(`Refusing: ${DB}/${TABLE} does not look like spike data`);
const db = new TablesDB(
  new Client().setEndpoint(process.env.APPWRITE_ENDPOINT).setProject(process.env.APPWRITE_PROJECT_ID).setKey(process.env.APPWRITE_API_KEY),
);
await db.deleteTable({ databaseId: DB, tableId: TABLE });
console.log(`deleted table ${DB}/${TABLE}`);
