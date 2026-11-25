import { describe, expect, it } from "vitest";
import { decideAccess } from "@/lib/access";

describe("auth redirect when unauthenticated", () => {
  it("redirects a protected page to /login with the return path", () => {
    expect(decideAccess({ pathname: "/payments", search: "?a=1", hasSession: false })).toEqual({
      action: "redirect",
      to: "/login?next=%2Fpayments%3Fa%3D1",
    });
  });
  it("lets the login page and the session route through without a cookie", () => {
    expect(decideAccess({ pathname: "/login", search: "", hasSession: false }).action).toBe("next");
    expect(decideAccess({ pathname: "/api/session", search: "", hasSession: false }).action).toBe(
      "next",
    );
  });
  it("sends a signed-in admin away from /login", () => {
    expect(decideAccess({ pathname: "/login", search: "", hasSession: true })).toEqual({
      action: "redirect",
      to: "/",
    });
  });
  it("lets a session cookie through to protected pages (validity is checked in the layout)", () => {
    expect(decideAccess({ pathname: "/payments", search: "", hasSession: true }).action).toBe(
      "next",
    );
  });
  it("never redirects to an off-site next parameter", async () => {
    const { safeNext } = await import("@/lib/access");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("/payments")).toBe("/payments");
    expect(safeNext(undefined)).toBe("/");
  });
});

describe("safeNext open-redirect guard", () => {
  it.each([
    "//evil.com",
    "https://evil.com",
    "/\\evil.com",
    "/\t/evil.com",
    "/\n/evil.com",
    "/\r/evil.com",
    "/ /evil.com",
    "/\u0000/evil.com",
    "javascript:alert(1)",
    "evil.com",
    "",
  ])("rejects %j", async (v) => {
    const { safeNext } = await import("@/lib/access");
    expect(safeNext(v)).toBe("/");
  });
  it("keeps same-site paths with query and encoded characters", async () => {
    const { safeNext } = await import("@/lib/access");
    expect(safeNext("/payments?page=2&q=a%20b")).toBe("/payments?page=2&q=a%20b");
  });
  it("resolved result always stays on the same origin", async () => {
    const { safeNext } = await import("@/lib/access");
    for (const v of ["/%09/evil.com", "/%5Cevil.com", "/a/../../evil.com"]) {
      expect(new URL(safeNext(v), "http://h.test").origin).toBe("http://h.test");
    }
  });
});
