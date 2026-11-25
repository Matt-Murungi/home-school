import "server-only";
import { Client, TablesDB, type Models } from "node-appwrite";

// Spike only: the API key is used on the server so the spike can run without
// admin login. The real dashboard (F06) must act as the signed-in admin via an
// Appwrite session so row permissions apply (SR §6.3, ADR 0004).
function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing environment variable ${name} (see .env.example)`);
  return value;
}

export const DATABASE_ID = process.env.APPWRITE_DATABASE_ID ?? "admin_spike";
export const SESSIONS_TABLE_ID = process.env.APPWRITE_SESSIONS_TABLE_ID ?? "sessions";

export const SUBJECTS = ["English", "Mathematics", "Science", "Social Studies", "Literacy"] as const;
export const STATUSES = ["scheduled", "completed", "confirmed", "disputed", "cancelled"] as const;

export type SessionRow = Models.Row & {
  child_name: string;
  teacher_name: string;
  subject: (typeof SUBJECTS)[number];
  status: (typeof STATUSES)[number];
  scheduled_at: string;
  amount_ugx: number;
};

export function tablesDB(): TablesDB {
  const client = new Client()
    .setEndpoint(required("APPWRITE_ENDPOINT"))
    .setProject(required("APPWRITE_PROJECT_ID"))
    .setKey(required("APPWRITE_API_KEY"));
  return new TablesDB(client);
}
