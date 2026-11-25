import { NextResponse, type NextRequest } from "next/server";
import { isSameOriginPost, safeNext } from "@/lib/access";
import { signIn, verifyMfa } from "@/lib/auth/flow";
import {
  PENDING_MAX_AGE_SECONDS,
  abandonPending,
  openPending,
  sealPending,
} from "@/lib/auth/pending";
import {
  MFA_COOKIE,
  SESSION_COOKIE,
  authProvider,
  cookieOptions,
  nowMs,
  pendingKey,
} from "@/lib/session";

const SESSION_SECONDS = 60 * 60 * 8;

function back(req: NextRequest, path: string) {
  // 303 so the browser turns the POST into a GET.
  return NextResponse.redirect(new URL(path, req.url), 303);
}

const bad = (status: number, text: string) => new NextResponse(text, { status });

/**
 * The only place the session cookie is set. Same-origin form POST only (no GET handler, so logout
 * cannot be triggered by a link or image): step=password | mfa | cancel | logout.
 */
export async function POST(req: NextRequest) {
  if (!isSameOriginPost(req.headers)) return bad(403, "Cross-site request refused");
  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return bad(400, "Expected a form submission");
  }
  const str = (k: string) => (typeof form.get(k) === "string" ? (form.get(k) as string) : "");
  const next = safeNext(str("next"));
  const nextQ = next === "/" ? "" : `&next=${encodeURIComponent(next)}`;
  const auth = authProvider();
  const step = str("step");

  // The pending-MFA cookie is signed with a server key and carries a server-checked issue time.
  const pendingCookie = req.cookies.get(MFA_COOKIE)?.value ?? "";
  const opened = pendingCookie ? openPending(pendingCookie, pendingKey(), nowMs()) : null;
  const dropPending = async () => {
    if (opened && opened.status !== "invalid") await abandonPending(auth, opened.pending);
  };

  if (step === "logout") {
    const secret = req.cookies.get(SESSION_COOKIE)?.value;
    if (secret) await auth.deleteSession(secret);
    const res = back(req, "/login");
    res.cookies.delete(SESSION_COOKIE);
    res.cookies.delete(MFA_COOKIE);
    return res;
  }

  if (step === "cancel") {
    await dropPending();
    const res = back(req, "/login");
    res.cookies.delete(MFA_COOKIE);
    return res;
  }

  if (step === "password") {
    await dropPending(); // a stale half-finished sign-in is replaced, not left alive
    const r = await signIn(auth, str("email"), str("password"));
    if (r.status === "error")
      return back(req, `/login?error=${encodeURIComponent(r.message)}${nextQ}`);
    if (r.status === "mfa_required") {
      const res = back(req, `/login?step=mfa${nextQ}`);
      res.cookies.set(
        MFA_COOKIE,
        sealPending(r.pendingSession, pendingKey(), nowMs()),
        cookieOptions(PENDING_MAX_AGE_SECONDS),
      );
      return res;
    }
    return back(req, "/login");
  }

  if (step === "mfa") {
    if (!opened || opened.status === "invalid") {
      const res = back(req, `/login?error=${encodeURIComponent("Sign-in expired. Start again.")}`);
      res.cookies.delete(MFA_COOKIE);
      return res;
    }
    if (opened.status === "expired") {
      await dropPending();
      const res = back(req, `/login?expired=1`);
      res.cookies.delete(MFA_COOKIE);
      return res;
    }
    const pending = opened.pending;
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
