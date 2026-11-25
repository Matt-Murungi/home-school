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
    expect(decideAccess({ pathname: "/api/session", search: "", hasSession: false }).action).toBe("next");
  });
  it("sends a signed-in admin away from /login", () => {
    expect(decideAccess({ pathname: "/login", search: "", hasSession: true })).toEqual({ action: "redirect", to: "/" });
  });
  it("lets a session cookie through to protected pages (validity is checked in the layout)", () => {
    expect(decideAccess({ pathname: "/payments", search: "", hasSession: true }).action).toBe("next");
  });
  it("never redirects to an off-site next parameter", async () => {
    const { safeNext } = await import("@/lib/access");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("/payments")).toBe("/payments");
    expect(safeNext(undefined)).toBe("/");
  });
});
