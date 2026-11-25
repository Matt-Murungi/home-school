// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeAuthProvider } from "@/lib/auth/fake";

let auth: FakeAuthProvider;
let cookieValue: string | undefined;
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
const listRows = vi.fn(async () => ({ rows: [] }));
vi.mock("node-appwrite", async (orig) => ({
  ...(await orig<typeof import("node-appwrite")>()),
  TablesDB: class {
    listRows = listRows;
  },
}));

import { POST } from "@/app/api/search/route";

async function signIn() {
  auth = new FakeAuthProvider([
    {
      email: "o@x.test",
      password: "pw-pw-pw-pw",
      name: "o",
      labels: ["opsadmin"],
      mfa: "totp" as const,
      code: "123456",
    },
  ]);
  const s = await auth.createPasswordSession("o@x.test", "pw-pw-pw-pw");
  await auth.completeMfa(s!, (await auth.startMfa(s!)).challengeId, "123456");
  cookieValue = s!;
}
const req = () =>
  new NextRequest("http://admin.test/api/search", {
    method: "POST",
    body: JSON.stringify({ q: "grace" }),
    headers: {
      host: "admin.test",
      "content-type": "application/json",
      cookie: `hs_admin_session=${cookieValue}`,
    },
  });

const saved = { ...process.env };
beforeEach(async () => {
  listRows.mockClear();
  process.env.APPWRITE_ENDPOINT = "https://aw.test/v1";
  process.env.APPWRITE_PROJECT_ID = "p";
  delete process.env.CORE_API_URL;
  await signIn();
});
afterEach(() => {
  process.env = { ...saved };
});

describe("POST /api/search with the real wiring (no mocked searchDeps)", () => {
  it.each(["staging", "production"])(
    "%s without CORE_API_URL: 503 and no data read",
    async (env) => {
      process.env.ADMIN_ENV = env;
      const res = await POST(req());
      expect(res.status).toBe(503);
      expect(await res.json()).toEqual({ error: "unavailable" });
      expect(listRows).not.toHaveBeenCalled();
    },
  );
  it("staging with a non-TLS core URL: 503 and no data read", async () => {
    process.env.ADMIN_ENV = "staging";
    process.env.CORE_API_URL = "http://core.internal";
    expect((await POST(req())).status).toBe(503);
    expect(listRows).not.toHaveBeenCalled();
  });
  it("a deployed search whose audit write fails returns 503 and no results", async () => {
    process.env.ADMIN_ENV = "staging";
    process.env.CORE_API_URL = "https://core.example.test";
    const f = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (u) =>
        String(u).includes("/account/jwts")
          ? new Response(JSON.stringify({ jwt: "J" }), { status: 201 })
          : new Response("{}", { status: 503 }),
      );
    const res = await POST(req());
    f.mockRestore();
    // personal types are audited before results leave: a failed audit write must not return data
    expect(res.status).toBe(503);
  });
});
