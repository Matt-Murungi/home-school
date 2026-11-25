// @vitest-environment node
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FakeAuthProvider } from "@/lib/auth/fake";
import { signIn } from "@/lib/auth/flow";
import { openPending, sealPending } from "@/lib/auth/pending";

const KEY = "k".repeat(40);
let auth: FakeAuthProvider;
let now = 1_700_000_000_000;

vi.mock("@/lib/session", () => ({
  MFA_COOKIE: "hs_admin_mfa",
  SESSION_COOKIE: "hs_admin_session",
  cookieOptions: (maxAge: number) => ({ httpOnly: true, path: "/", maxAge }),
  authProvider: () => auth,
  pendingKey: () => KEY,
  nowMs: () => now,
}));

import { POST } from "@/app/api/session/route";
import * as route from "@/app/api/session/route";
import { GET as clearGET } from "@/app/api/session/clear/route";

const users = [
  {
    email: "o@x.test",
    password: "pw-pw-pw-pw",
    name: "O",
    labels: ["opsadmin"],
    mfa: "totp" as const,
    code: "123456",
  },
];
const HOST = "http://admin.test";

function post(
  body: string | URLSearchParams | null,
  headers: Record<string, string> = {},
  cookie = "",
) {
  const h: Record<string, string> = { ...headers };
  if (cookie) h.cookie = cookie;
  if (body instanceof URLSearchParams) h["content-type"] = "application/x-www-form-urlencoded";
  return new NextRequest(`${HOST}/api/session`, {
    method: "POST",
    body: body === null ? undefined : body.toString(),
    headers: { host: "admin.test", ...h },
  });
}
const form = (o: Record<string, string>) => new URLSearchParams(o);
const setCookie = (res: Response) => res.headers.getSetCookie().join("\n");

beforeEach(() => {
  auth = new FakeAuthProvider(users);
  now = 1_700_000_000_000;
});

describe("POST /api/session hardening", () => {
  it("a non-form POST (JSON, none, junk) returns 400, not 500", async () => {
    for (const r of [
      post(JSON.stringify({ step: "logout" }), { "content-type": "application/json" }),
      post(null),
      post("step=logout", { "content-type": "text/plain" }),
      post("--x", { "content-type": "multipart/form-data; boundary=zzz" }),
    ]) {
      const res = await POST(r);
      expect(res.status).toBe(400);
    }
  });
  it("rejects cross-site POSTs (logout and login CSRF) with 403 and sets no cookie", async () => {
    for (const h of <Record<string, string>[]>[
      { origin: "https://evil.example" },
      { "sec-fetch-site": "cross-site" },
      { "sec-fetch-site": "same-site" },
    ]) {
      const res = await POST(post(form({ step: "logout" }), h, "hs_admin_session=abc"));
      expect(res.status).toBe(403);
      expect(setCookie(res)).toBe("");
    }
  });
  it("accepts a same-origin POST (Origin matches, or Sec-Fetch-Site same-origin)", async () => {
    const a = await POST(post(form({ step: "logout" }), { origin: HOST }));
    const b = await POST(post(form({ step: "logout" }), { "sec-fetch-site": "same-origin" }));
    expect(a.status).toBe(303);
    expect(b.status).toBe(303);
  });
  it("logout is POST-only: the route exports no GET/PUT/DELETE", () => {
    expect(Object.keys(route).filter((k) => /^(GET|PUT|PATCH|DELETE|HEAD)$/.test(k))).toEqual([]);
  });
  it("a very long next is ignored", async () => {
    const res = await POST(
      post(form({ step: "password", email: "x", password: "y", next: "/" + "a".repeat(5000) }), {
        origin: HOST,
      }),
    );
    expect(res.headers.get("location")).not.toContain("aaaa");
  });
});

describe("pending-MFA cookie lifecycle", () => {
  async function startMfa() {
    const res = await POST(
      post(form({ step: "password", email: "o@x.test", password: "pw-pw-pw-pw" }), {
        origin: HOST,
      }),
    );
    const raw = /hs_admin_mfa=([^;]+)/.exec(setCookie(res))![1];
    return decodeURIComponent(raw);
  }
  it("sets a signed cookie, and the right code within 10 minutes completes sign-in", async () => {
    const c = await startMfa();
    expect(openPending(c, KEY, now).status).toBe("ok");
    now += 9 * 60_000;
    const res = await POST(
      post(
        form({ step: "mfa", code: "123456" }),
        { origin: HOST },
        `hs_admin_mfa=${encodeURIComponent(c)}`,
      ),
    );
    expect(setCookie(res)).toContain("hs_admin_session=");
  });
  it("after 10 minutes the server refuses the replayed cookie and deletes the password-only session", async () => {
    const c = await startMfa();
    expect(auth.liveSessions()).toBe(1);
    now += 11 * 60_000;
    const res = await POST(
      post(
        form({ step: "mfa", code: "123456" }),
        { origin: HOST },
        `hs_admin_mfa=${encodeURIComponent(c)}`,
      ),
    );
    expect(setCookie(res)).not.toContain("hs_admin_session=");
    expect(res.headers.get("location")).toContain("expired");
    expect(auth.liveSessions()).toBe(0);
  });
  it("cancel deletes the pending session and clears the cookie", async () => {
    const c = await startMfa();
    const res = await POST(
      post(form({ step: "cancel" }), { origin: HOST }, `hs_admin_mfa=${encodeURIComponent(c)}`),
    );
    expect(auth.liveSessions()).toBe(0);
    expect(setCookie(res)).toMatch(/hs_admin_mfa=;/);
    expect(res.headers.get("location")).toContain("/login");
  });
  it("starting a new password sign-in discards a stale pending session", async () => {
    const c = await startMfa();
    const res = await POST(
      post(
        form({ step: "password", email: "o@x.test", password: "pw-pw-pw-pw" }),
        { origin: HOST },
        `hs_admin_mfa=${encodeURIComponent(c)}`,
      ),
    );
    expect(res.status).toBe(303);
    expect(auth.liveSessions()).toBe(1);
  });
  it("a forged unsigned pending cookie is refused and touches nothing", async () => {
    const s = await auth.createPasswordSession("o@x.test", "pw-pw-pw-pw");
    const forged = JSON.stringify({ secret: s, challengeId: "c", factor: "totp" });
    const res = await POST(
      post(
        form({ step: "mfa", code: "123456" }),
        { origin: HOST },
        `hs_admin_mfa=${encodeURIComponent(forged)}`,
      ),
    );
    expect(setCookie(res)).not.toContain("hs_admin_session=");
    expect(sealPending).toBeDefined();
  });
});

describe("GET /api/session/clear (internal redirect target for invalid sessions)", () => {
  const get = (cookie?: string, h: Record<string, string> = {}) =>
    new NextRequest(`${HOST}/api/session/clear`, {
      headers: { host: "admin.test", ...(cookie ? { cookie } : {}), ...h },
    });

  it("drops an invalid cookie and returns to login (expired-session path keeps working)", async () => {
    const res = await clearGET(get("hs_admin_session=bogus"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(`${HOST}/login?expired=1`);
    expect(setCookie(res)).toMatch(/hs_admin_session=;/);
  });
  it("does NOT log out a valid admin session (a cross-site link cannot sign anyone out)", async () => {
    const r = await signIn(auth, "o@x.test", "pw-pw-pw-pw");
    if (r.status !== "mfa_required") throw new Error();
    await auth.completeMfa(r.pendingSession.secret, r.pendingSession.challengeId, "123456");
    const res = await clearGET(
      get(`hs_admin_session=${r.pendingSession.secret}`, { "sec-fetch-site": "cross-site" }),
    );
    expect(setCookie(res)).not.toMatch(/hs_admin_session=;/);
    expect(res.headers.get("location")).toBe(`${HOST}/`);
    expect(auth.liveSessions()).toBe(1);
  });
  it("a valid but non-admin (password-only) session is cleared and revoked server-side", async () => {
    const s = await auth.createPasswordSession("o@x.test", "pw-pw-pw-pw");
    const res = await clearGET(get(`hs_admin_session=${s}`));
    expect(setCookie(res)).toMatch(/hs_admin_session=;/);
    expect(auth.liveSessions()).toBe(0);
  });
  it("without a cookie it just goes to login", async () => {
    const res = await clearGET(get());
    expect(res.headers.get("location")).toBe(`${HOST}/login?expired=1`);
  });
});
