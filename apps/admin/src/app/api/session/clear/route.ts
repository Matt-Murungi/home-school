import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/access";
import { adminFromState } from "@/lib/auth/guard";
import { authProvider } from "@/lib/session";

/**
 * Internal redirect target of `requireAccess` / the admin layout when the session is not a valid
 * admin session. It is a GET, so any site can link to it; therefore it clears the cookie ONLY when
 * the cookie is not a valid admin session (a valid admin is sent to the overview instead, nothing
 * is cleared or revoked). A valid but non-admin session (e.g. password-only) is also revoked at
 * Appwrite. Real sign-out is the same-origin POST `/api/session` with step=logout.
 */
export async function GET(req: NextRequest) {
  const secret = req.cookies.get(SESSION_COOKIE)?.value;
  if (secret) {
    const auth = authProvider();
    const state = await auth.accountState(secret);
    if (adminFromState(state)) return NextResponse.redirect(new URL("/", req.url));
    if (state.status !== "invalid") {
      try {
        await auth.deleteSession(secret);
      } catch {
        /* best effort */
      }
    }
  }
  const res = NextResponse.redirect(new URL("/login?expired=1", req.url));
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
