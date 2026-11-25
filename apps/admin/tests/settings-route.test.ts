// @vitest-environment node
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FakeAuthProvider } from "@/lib/auth/fake";
import { canAccessPath } from "@/lib/roles";
import { scanApp } from "./helpers/guard-scan";
import { join } from "node:path";

let auth: FakeAuthProvider;
let cookieValue: string | undefined;
let secretSeen: string | undefined;
const core = {
  list: vi.fn(async () => ({ groups: [], settings: [] })),
  history: vi.fn(async () => ({ key: "k", changes: [], next_before: null })),
  change: vi.fn(async () => ({ key: "k" })),
};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (n: string) =>
      n === "hs_admin_session" && cookieValue ? { value: cookieValue } : undefined,
  }),
  headers: async () => new Headers(),
}));
vi.mock("@/lib/auth/appwrite", () => ({
  AppwriteAuthProvider: class {
    constructor() {
      return auth;
    }
  },
}));
vi.mock("@/lib/settings/server", () => ({
  settingsClient: (secret: string) => {
    secretSeen = secret;
    return core;
  },
}));

import * as route from "@/app/api/settings/route";

const users = (label: string) => ({
  email: `${label}@x.test`,
  password: "pw-pw-pw-pw",
  name: label,
  labels: [label],
  mfa: "totp" as const,
  code: "123456",
});
async function signIn(label: string) {
  const s = await auth.createPasswordSession(`${label}@x.test`, "pw-pw-pw-pw");
  await auth.completeMfa(s!, (await auth.startMfa(s!)).challengeId, "123456");
  cookieValue = s!;
}
const HOST = "http://admin.test";
const REASON = "Owner decision recorded in the meeting";
const body = {
  op: "set",
  key: "session.auto_confirm_after_hours",
  value: 24,
  reason: REASON,
  revision: 0,
};
function post(b: unknown, headers: Record<string, string> = {}, raw?: string) {
  return new NextRequest(`${HOST}/api/settings`, {
    method: "POST",
    body: raw ?? JSON.stringify(b),
    headers: {
      host: "admin.test",
      "content-type": "application/json",
      ...(cookieValue ? { cookie: `hs_admin_session=${cookieValue}` } : {}),
      ...headers,
    },
  });
}
const get = (q = "") =>
  new NextRequest(`${HOST}/api/settings${q}`, {
    headers: {
      host: "admin.test",
      ...(cookieValue ? { cookie: `hs_admin_session=${cookieValue}` } : {}),
    },
  });
const redirectOf = async (p: Promise<Response>) => {
  try {
    await p;
  } catch (e) {
    return String((e as { digest?: string }).digest ?? e);
  }
  return null;
};

beforeEach(() => {
  auth = new FakeAuthProvider(
    ["opsadmin", "curator", "superadmin", "safeguardinglead", "parent"].map(users),
  );
  cookieValue = undefined;
  secretSeen = undefined;
  for (const f of Object.values(core)) f.mockClear();
});

describe("/api/settings (role guard, F06 requireAccess pattern)", () => {
  it("is guarded by the route literal and the role table allows only the super admin", () => {
    const { violations, checked } = scanApp(join(__dirname, "../src"), {
      "layout.tsx": "Root layout: html shell only, no data and no session information.",
      "login/page.tsx":
        "Sign-in page: reachable without a session by design (see page-guard test).",
      "(admin)/forbidden/page.tsx": "Static no-access page the guard redirects to; shows no data.",
      "api/session/route.ts": "Login, MFA and logout form POSTs: they establish the session.",
      "api/session/clear/route.ts": "Internal redirect target for an invalid session; clears it.",
    });
    expect(violations).toEqual([]);
    expect(checked).toContain("api/settings/route.ts");
    expect(canAccessPath("/api/settings", ["superadmin"])).toBe(true);
    for (const r of ["opsadmin", "curator", "safeguardinglead"] as const)
      expect(canAccessPath("/api/settings", [r])).toBe(false);
    expect(canAccessPath("/settings", ["superadmin"])).toBe(true);
    expect(canAccessPath("/settings", ["opsadmin"])).toBe(false);
  });
  it.each(["GET", "POST"])(
    "%s: unauthenticated is redirected and the core is never called",
    async (m) => {
      const r = m === "GET" ? route.GET(get()) : route.POST(post(body));
      expect(await redirectOf(r)).toMatch(/\/api\/session\/clear/);
      expect(core.list).not.toHaveBeenCalled();
      expect(core.change).not.toHaveBeenCalled();
    },
  );
  it.each(["opsadmin", "curator", "safeguardinglead", "parent"])(
    "%s is refused on both methods and the core is never called",
    async (label) => {
      await signIn(label);
      expect(await redirectOf(route.GET(get()))).toMatch(
        /NEXT_REDIRECT;.*(\/forbidden|\/api\/session\/clear)/,
      );
      expect(await redirectOf(route.POST(post(body)))).toMatch(
        /NEXT_REDIRECT;.*(\/forbidden|\/api\/session\/clear)/,
      );
      expect(core.list).not.toHaveBeenCalled();
      expect(core.change).not.toHaveBeenCalled();
    },
  );
  it("the super admin lists, reads history and changes, as themselves (own session secret)", async () => {
    await signIn("superadmin");
    const res = await route.GET(get());
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toMatch(/no-store/);
    expect(secretSeen).toBe(cookieValue);
    expect((await route.GET(get("?key=notify.rate_limit&before=3"))).status).toBe(200);
    expect(core.history).toHaveBeenCalledWith("notify.rate_limit", 3);
    const r = await route.POST(post(body));
    expect(r.status).toBe(200);
    expect(core.change).toHaveBeenCalledWith(body);
    expect(r.headers.getSetCookie()).toEqual([]);
  });
});

describe("POST hardening", () => {
  it("cross-site posts are refused before anything is read", async () => {
    await signIn("superadmin");
    const r = await route.POST(post(body, { "sec-fetch-site": "cross-site" }));
    expect(r.status).toBe(403);
    const o = await route.POST(post(body, { origin: "https://evil.test" }));
    expect(o.status).toBe(403);
    expect(core.change).not.toHaveBeenCalled();
  });
  it("needs JSON, caps the size, refuses malformed JSON and unknown fields", async () => {
    await signIn("superadmin");
    expect((await route.POST(post(body, { "content-type": "text/plain" }))).status).toBe(400);
    expect((await route.POST(post(null, {}, "x".repeat(9000)))).status).toBe(413);
    expect((await route.POST(post(null, {}, "{not json"))).status).toBe(400);
    expect((await route.POST(post({ ...body, actor_id: "u_evil" }))).status).toBe(400);
    expect(core.change).not.toHaveBeenCalled();
  });
  it("a core outage is a 503 with no detail", async () => {
    await signIn("superadmin");
    core.change.mockRejectedValueOnce(new Error("password=hunter2 stack"));
    const r = await route.POST(post(body));
    expect(r.status).toBe(503);
    expect(JSON.stringify(await r.json())).not.toMatch(/hunter2|stack/);
  });
});
