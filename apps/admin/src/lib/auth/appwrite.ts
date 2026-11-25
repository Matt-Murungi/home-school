import "server-only";
import { Account as AppwriteAccount, AppwriteException, AuthenticationFactor, Client } from "node-appwrite";
import type { AdminConfig } from "../config";
import type { AccountState, AuthProvider, MfaFactor } from "./types";

/**
 * Appwrite Auth over the SSR session pattern. No API key: every call is made with a
 * client scoped to the signed-in admin's session secret (setSession), so row permissions
 * and the audit trail apply to that admin (ADR 0004, SR 6.3).
 * NOT yet exercised against a real project: needs the staging project (memory.md, Blocked on owner).
 */
export class AppwriteAuthProvider implements AuthProvider {
  constructor(private cfg: AdminConfig) {}

  private client(secret?: string) {
    const c = new Client().setEndpoint(this.cfg.endpoint).setProject(this.cfg.projectId);
    return secret ? c.setSession(secret) : c;
  }

  async createPasswordSession(email: string, password: string) {
    try {
      const s = await new AppwriteAccount(this.client()).createEmailPasswordSession(email, password);
      return s.secret || null;
    } catch (e) {
      if (e instanceof AppwriteException && e.code >= 400 && e.code < 500) return null;
      throw e;
    }
  }

  async accountState(secret: string): Promise<AccountState> {
    const account = new AppwriteAccount(this.client(secret));
    try {
      const u = await account.get();
      return { status: "ok", account: { id: u.$id, name: u.name, email: u.email, labels: u.labels } };
    } catch (e) {
      if (e instanceof AppwriteException) {
        if (e.type === "user_more_factors_required") {
          const f = await account.listMfaFactors();
          return { status: "mfa_required", factor: f.totp ? "totp" : "email" };
        }
        if (e.code === 401) return { status: "invalid" };
      }
      throw e;
    }
  }

  async startMfa(secret: string, factor: MfaFactor) {
    const ch = await new AppwriteAccount(this.client(secret)).createMfaChallenge(
      factor === "totp" ? AuthenticationFactor.Totp : AuthenticationFactor.Email,
    );
    return { challengeId: ch.$id };
  }

  async completeMfa(secret: string, challengeId: string, code: string) {
    try {
      await new AppwriteAccount(this.client(secret)).updateMfaChallenge(challengeId, code);
      return true;
    } catch (e) {
      if (e instanceof AppwriteException && e.code >= 400 && e.code < 500) return false;
      throw e;
    }
  }

  async deleteSession(secret: string) {
    try {
      await new AppwriteAccount(this.client(secret)).deleteSession("current");
    } catch {
      /* already gone */
    }
  }
}
