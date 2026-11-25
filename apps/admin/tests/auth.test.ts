import { describe, expect, it } from "vitest";
import { FakeAuthProvider } from "@/lib/auth/fake";
import { signIn, verifyMfa } from "@/lib/auth/flow";

const users = [
  {
    email: "ops@x.test",
    password: "correct-horse",
    name: "Ops",
    labels: ["opsadmin"],
    mfa: "totp" as const,
    code: "123456",
  },
  {
    email: "parent@x.test",
    password: "pw-pw-pw-pw",
    name: "Parent",
    labels: ["parent"],
    mfa: "totp" as const,
    code: "111111",
  },
];

describe("sign-in flow with the MFA step interface", () => {
  it("password ok -> MFA required -> correct code -> session", async () => {
    const auth = new FakeAuthProvider(users);
    const first = await signIn(auth, "ops@x.test", "correct-horse");
    expect(first).toMatchObject({ status: "mfa_required", factor: "totp" });
    if (first.status !== "mfa_required") throw new Error();
    const done = await verifyMfa(auth, first.pendingSession, "123456");
    expect(done.status).toBe("ok");
  });
  it("wrong password gives a generic error", async () => {
    const r = await signIn(new FakeAuthProvider(users), "ops@x.test", "nope");
    expect(r).toEqual({ status: "error", message: "Email or password is incorrect." });
  });
  it("wrong MFA code is rejected", async () => {
    const auth = new FakeAuthProvider(users);
    const first = await signIn(auth, "ops@x.test", "correct-horse");
    if (first.status !== "mfa_required") throw new Error();
    expect((await verifyMfa(auth, first.pendingSession, "000000")).status).toBe("error");
  });
  it("a non-admin account is refused after MFA and its session is deleted", async () => {
    const auth = new FakeAuthProvider(users);
    const first = await signIn(auth, "parent@x.test", "pw-pw-pw-pw");
    if (first.status !== "mfa_required") throw new Error();
    const r = await verifyMfa(auth, first.pendingSession, "111111");
    expect(r).toEqual({ status: "error", message: "This account is not an admin." });
    expect(auth.liveSessions()).toBe(0);
  });
  it("an admin without MFA enrolled is refused (2FA required)", async () => {
    const auth = new FakeAuthProvider([
      { email: "n@x.test", password: "pw-pw-pw-pw", name: "N", labels: ["curator"] },
    ]);
    const r = await signIn(auth, "n@x.test", "pw-pw-pw-pw");
    expect(r).toEqual({
      status: "error",
      message: "Two-factor authentication must be set up for admin accounts.",
    });
    expect(auth.liveSessions()).toBe(0);
  });
  it("blank input is rejected before reaching the provider", async () => {
    expect((await signIn(new FakeAuthProvider(users), "", "")).status).toBe("error");
  });
});

describe("admin guard (MFA enforced on every request, not just at login)", () => {
  it("refuses a valid password-only session of an account with no MFA", async () => {
    const { adminFromState } = await import("@/lib/auth/guard");
    const auth = new FakeAuthProvider([
      { email: "n@x.y", password: "pw", name: "N", labels: ["superadmin"] },
    ]);
    const secret = (await auth.createPasswordSession("n@x.y", "pw"))!;
    const state = await auth.accountState(secret);
    expect(state.status).toBe("ok");
    expect(adminFromState(state)).toBeNull();
  });
  it("accepts MFA-enrolled admins with a label and refuses labels with other casing or underscores", async () => {
    const { adminFromState } = await import("@/lib/auth/guard");
    const mk = (labels: string[], mfaEnrolled = true) =>
      adminFromState({
        status: "ok",
        account: { id: "1", name: "A", email: "a@x.y", labels, mfaEnrolled },
      });
    expect(mk(["opsadmin"])?.roles).toEqual(["opsadmin"]);
    expect(mk(["ops_admin"])).toBeNull();
    expect(mk(["OpsAdmin"])).toBeNull();
    expect(mk(["parent"])).toBeNull();
    expect(mk(["opsadmin"], false)).toBeNull();
    expect(adminFromState({ status: "mfa_required", factor: "totp" })).toBeNull();
    expect(adminFromState({ status: "invalid" })).toBeNull();
  });
});
