import type { AdminRole } from "../roles";

export type MfaFactor = "totp" | "email";
export type Account = { id: string; name: string; email: string; labels: string[] };
export type AdminAccount = { id: string; name: string; email: string; roles: AdminRole[] };

export type AccountState =
  | { status: "ok"; account: Account }
  | { status: "mfa_required"; factor: MfaFactor }
  | { status: "invalid" };

/** Everything the admin needs from Appwrite Auth. Real impl: appwrite.ts; test impl: fake.ts. */
export interface AuthProvider {
  /** Email + password session. Returns the session secret (never the API key). */
  createPasswordSession(email: string, password: string): Promise<string | null>;
  accountState(sessionSecret: string): Promise<AccountState>;
  startMfa(sessionSecret: string, factor: MfaFactor): Promise<{ challengeId: string }>;
  completeMfa(sessionSecret: string, challengeId: string, code: string): Promise<boolean>;
  deleteSession(sessionSecret: string): Promise<void>;
}
