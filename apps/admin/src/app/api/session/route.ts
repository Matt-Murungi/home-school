import { NextResponse, type NextRequest } from "next/server";
import { safeNext } from "@/lib/access";
import { signIn, verifyMfa, type PendingSession } from "@/lib/auth/flow";
import { MFA_COOKIE, SESSION_COOKIE, authProvider, cookieOptions } from "@/lib/session";

const SESSION_SECONDS = 60 * 60 * 8;

function back(req: NextRequest, path: string) {
  // 303 so the browser turns the POST into a GET.
  return NextResponse.redirect(new URL(path, req.url), 303);
}

/** The only place the session cookie is set. Form POST: step=password | mfa | logout. */
export async function POST(req: NextRequest) {
  const form = await req.formData();
  const str = (k: string) => (typeof form.get(k) === "string" ? (form.get(k) as string) : "");
  const next = safeNext(str("next"));
  const nextQ = next === "/" ? "" : `&next=${encodeURIComponent(next)}`;
  const auth = authProvider();
  const step = str("step");

  if (step === "logout") {
    const secret = req.cookies.get(SESSION_COOKIE)?.value;
    if (secret) await auth.deleteSession(secret);
    const res = back(req, "/login");
    res.cookies.delete(SESSION_COOKIE);
    res.cookies.delete(MFA_COOKIE);
    return res;
  }

  if (step === "password") {
    const r = await signIn(auth, str("email"), str("password"));
    if (r.status === "error") return back(req, `/login?error=${encodeURIComponent(r.message)}${nextQ}`);
    if (r.status === "mfa_required") {
      const res = back(req, `/login?step=mfa${nextQ}`);
      res.cookies.set(MFA_COOKIE, JSON.stringify(r.pendingSession), cookieOptions(60 * 10));
      return res;
    }
    return back(req, "/login");
  }

  if (step === "mfa") {
    let pending: PendingSession | null = null;
    try {
      pending = JSON.parse(req.cookies.get(MFA_COOKIE)?.value ?? "null");
    } catch {
      pending = null;
    }
    if (!pending?.secret) return back(req, `/login?error=${encodeURIComponent("Sign-in expired. Start again.")}`);
    const r = await verifyMfa(auth, pending, str("code"));
    if (r.status !== "ok") {
      const bad = r.status === "error" ? r.message : "Try again.";
      return back(req, `/login?step=mfa&error=${encodeURIComponent(bad)}${nextQ}`);
    }
    const res = back(req, next);
    res.cookies.set(SESSION_COOKIE, r.secret, cookieOptions(SESSION_SECONDS));
    res.cookies.delete(MFA_COOKIE);
    return res;
  }

  return back(req, "/login");
}
