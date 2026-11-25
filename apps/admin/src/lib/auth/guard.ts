import { rolesFromLabels } from "../roles";
import type { AccountState, AdminAccount } from "./types";

/**
 * The single rule for "this session is a signed-in admin": the session is valid, the account
 * has MFA enrolled (a password-only session minted straight at Appwrite is refused even if the
 * cookie is planted), and it holds at least one admin label.
 */
export function adminFromState(state: AccountState): AdminAccount | null {
  if (state.status !== "ok" || !state.account.mfaEnrolled) return null;
  const roles = rolesFromLabels(state.account.labels);
  if (roles.length === 0) return null;
  const { id, name, email } = state.account;
  return { id, name, email, roles };
}
