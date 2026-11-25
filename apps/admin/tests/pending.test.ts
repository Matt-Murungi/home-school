import { describe, expect, it } from "vitest";
import { FakeAuthProvider } from "@/lib/auth/fake";
import { signIn } from "@/lib/auth/flow";
import {
  PENDING_MAX_AGE_SECONDS,
  abandonPending,
  openPending,
  sealPending,
} from "@/lib/auth/pending";

const KEY = "k".repeat(40);
const P = { secret: "s1", challengeId: "c1", factor: "totp" as const };
const T0 = 1_700_000_000_000;

describe("pending-MFA cookie is signed and its age is checked by the server", () => {
  it("round-trips within 10 minutes", () => {
    const c = sealPending(P, KEY, T0);
    expect(openPending(c, KEY, T0 + 599_000)).toEqual({ status: "ok", pending: P });
  });
  it("is expired after 10 minutes even though the browser still sends it", () => {
    const c = sealPending(P, KEY, T0);
    expect(PENDING_MAX_AGE_SECONDS).toBe(600);
    expect(openPending(c, KEY, T0 + 601_000)).toEqual({ status: "expired", pending: P });
  });
  it("rejects a tampered payload, a forged iat, another key, junk and a legacy unsigned cookie", () => {
    const c = sealPending(P, KEY, T0);
    const [body, sig] = c.split(".");
    const forged = Buffer.from(
      JSON.stringify({ ...JSON.parse(Buffer.from(body, "base64url").toString()), iat: T0 + 9e9 }),
    ).toString("base64url");
    expect(openPending(`${forged}.${sig}`, KEY, T0 + 9e6)).toEqual({ status: "invalid" });
    expect(openPending(c, "z".repeat(40), T0)).toEqual({ status: "invalid" });
    expect(openPending("garbage", KEY, T0)).toEqual({ status: "invalid" });
    expect(openPending("", KEY, T0)).toEqual({ status: "invalid" });
    expect(openPending(JSON.stringify(P), KEY, T0)).toEqual({ status: "invalid" });
    expect(openPending(`${body}.`, KEY, T0)).toEqual({ status: "invalid" });
  });
  it("refuses to seal with a short key", () => {
    expect(() => sealPending(P, "short", T0)).toThrow();
  });
  it("an iat in the future is invalid", () => {
    const c = sealPending(P, KEY, T0 + 3_600_000);
    expect(openPending(c, KEY, T0)).toEqual({ status: "invalid" });
  });
});

describe("abandoned password-only sessions are deleted", () => {
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
  it("abandonPending deletes the Appwrite session; failures are swallowed", async () => {
    const auth = new FakeAuthProvider(users);
    const r = await signIn(auth, "o@x.test", "pw-pw-pw-pw");
    if (r.status !== "mfa_required") throw new Error();
    expect(auth.liveSessions()).toBe(1);
    await abandonPending(auth, r.pendingSession);
    expect(auth.liveSessions()).toBe(0);
    const broken = {
      ...auth,
      deleteSession: async () => {
        throw new Error("net");
      },
    } as unknown as FakeAuthProvider;
    await expect(abandonPending(broken, r.pendingSession)).resolves.toBeUndefined();
  });
});
