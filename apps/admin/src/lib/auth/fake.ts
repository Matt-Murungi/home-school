import type { Account, AccountState, AuthProvider, MfaFactor } from "./types";

export type FakeUser = {
  email: string;
  password: string;
  name: string;
  labels: string[];
  mfa?: MfaFactor;
  code?: string;
};

/** In-memory stand-in for Appwrite Auth used by tests and local UI work. */
export class FakeAuthProvider implements AuthProvider {
  private sessions = new Map<string, { user: FakeUser; mfaDone: boolean }>();
  private challenges = new Map<string, string>();
  private n = 0;
  constructor(private users: FakeUser[]) {}

  liveSessions() {
    return this.sessions.size;
  }
  async createPasswordSession(email: string, password: string) {
    const user = this.users.find((u) => u.email === email && u.password === password);
    if (!user) return null;
    const secret = `s${++this.n}`;
    this.sessions.set(secret, { user, mfaDone: !user.mfa });
    return secret;
  }
  async accountState(secret: string): Promise<AccountState> {
    const s = this.sessions.get(secret);
    if (!s) return { status: "invalid" };
    if (s.user.mfa && !s.mfaDone) return { status: "mfa_required", factor: s.user.mfa };
    const account: Account = {
      id: s.user.email,
      name: s.user.name,
      email: s.user.email,
      labels: s.user.labels,
      mfaEnrolled: !!s.user.mfa,
    };
    return { status: "ok", account };
  }
  async startMfa(secret: string) {
    const challengeId = `c${++this.n}`;
    this.challenges.set(challengeId, secret);
    return { challengeId };
  }
  async completeMfa(secret: string, challengeId: string, code: string) {
    const s = this.sessions.get(secret);
    if (!s || this.challenges.get(challengeId) !== secret || s.user.code !== code) return false;
    s.mfaDone = true;
    return true;
  }
  async deleteSession(secret: string) {
    this.sessions.delete(secret);
  }
}
