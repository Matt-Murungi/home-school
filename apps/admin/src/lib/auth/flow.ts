import { adminFromState } from "./guard";
import type { AdminAccount, AuthProvider, MfaFactor } from "./types";

export type PendingSession = { secret: string; challengeId: string; factor: MfaFactor };
export type SignInResult =
  | { status: "ok"; secret: string; admin: AdminAccount }
  | { status: "mfa_required"; factor: MfaFactor; pendingSession: PendingSession }
  | { status: "error"; message: string };

const BAD_CREDENTIALS = "Email or password is incorrect.";

export async function signIn(
  auth: AuthProvider,
  email: string,
  password: string,
): Promise<SignInResult> {
  if (!email.trim() || !password)
    return { status: "error", message: "Enter your email and password." };
  const secret = await auth.createPasswordSession(email.trim(), password);
  if (!secret) return { status: "error", message: BAD_CREDENTIALS };
  const state = await auth.accountState(secret);
  if (state.status === "invalid") return { status: "error", message: BAD_CREDENTIALS };
  if (state.status === "mfa_required") {
    const { challengeId } = await auth.startMfa(secret, state.factor);
    return {
      status: "mfa_required",
      factor: state.factor,
      pendingSession: { secret, challengeId, factor: state.factor },
    };
  }
  await auth.deleteSession(secret);
  return {
    status: "error",
    message: "Two-factor authentication must be set up for admin accounts.",
  };
}

export async function verifyMfa(
  auth: AuthProvider,
  p: PendingSession,
  code: string,
): Promise<SignInResult> {
  if (!/^\d{6}$/.test(code.trim())) return { status: "error", message: "Enter the 6-digit code." };
  if (!(await auth.completeMfa(p.secret, p.challengeId, code.trim()))) {
    return { status: "error", message: "That code is not correct or has expired." };
  }
  const state = await auth.accountState(p.secret);
  if (state.status !== "ok")
    return { status: "error", message: "Sign-in could not be completed. Try again." };
  const admin = adminFromState(state);
  if (!admin) {
    await auth.deleteSession(p.secret);
    return { status: "error", message: "This account is not an admin." };
  }
  return { status: "ok", secret: p.secret, admin };
}
