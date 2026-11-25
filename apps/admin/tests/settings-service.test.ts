// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { CoreSettingsClient, CoreSettingsError } from "@/lib/settings/core";
import { parseChange, runChange, runRead } from "@/lib/settings/service";
import type { AdminAccount } from "@/lib/auth/types";

const admin = (roles: AdminAccount["roles"]): AdminAccount => ({
  id: "u1",
  name: "A",
  email: "a@x.test",
  roles,
});
const REASON = "Owner decision recorded in the meeting";
const good = {
  op: "set",
  key: "session.auto_confirm_after_hours",
  value: 24,
  reason: REASON,
  revision: 0,
};

function client(over: Partial<Record<"change" | "list" | "history", unknown>> = {}) {
  return {
    change: vi.fn(async () => ({ ok: true })),
    list: vi.fn(async () => ({ groups: [], settings: [] })),
    history: vi.fn(async () => ({ key: "k", changes: [], next_before: null })),
    ...over,
  } as unknown as CoreSettingsClient;
}

describe("parseChange (mass assignment, hostile input)", () => {
  it("accepts a well-formed change", () => {
    expect(parseChange(good)).toEqual(good);
  });
  it.each([
    ["actor", { actor_id: "u_evil" }],
    ["actor camel", { actorId: "u_evil" }],
    ["roles", { roles: ["superadmin"] }],
    ["audit key", { requestId: "x", auditKey: "x" }],
    ["source", { source: "system" }],
    ["default", { default: 1 }],
  ])("refuses an unknown field: %s", (_n, extra) => {
    expect(parseChange({ ...good, ...extra })).toEqual({
      status: 400,
      body: { error: "unknown_field" },
    });
  });
  it.each([null, [], "x", 5, undefined])("refuses a non-object body %j", (b) => {
    expect(parseChange(b)).toMatchObject({ status: 400 });
  });
  it.each([
    [{ ...good, op: "delete" }, "invalid_op"],
    [{ ...good, op: undefined }, "invalid_op"],
    [{ ...good, key: "../x" }, "invalid_key"],
    [{ ...good, key: "Session.Auto" }, "invalid_key"],
    [{ ...good, key: "a".repeat(65) }, "invalid_key"],
    [{ ...good, key: 5 }, "invalid_key"],
    [{ ...good, reason: 5 }, "invalid_reason"],
    [{ ...good, reason: "x".repeat(2001) }, "invalid_reason"],
    [{ ...good, revision: -1 }, "invalid_revision"],
    [{ ...good, revision: 1.5 }, "invalid_revision"],
    [{ ...good, revision: "0" }, "invalid_revision"],
    [{ ...good, revision: Number.MAX_SAFE_INTEGER + 10 }, "invalid_revision"],
    [{ ...good, value: "24" }, "invalid_value"],
    [{ ...good, value: null }, "invalid_value"],
    [{ ...good, value: [1] }, "invalid_value"],
    [{ ...good, value: { a: "x".repeat(2000) } }, "invalid_value"],
    [{ ...good, effectiveAt: "not a date" }, "invalid_effective_at"],
    [{ ...good, effectiveAt: 5 }, "invalid_effective_at"],
  ])("refuses %j", (body, code) => {
    expect(parseChange(body)).toEqual({ status: 400, body: { error: code } });
  });
  it("normalises effectiveAt to a UTC instant", () => {
    const r = parseChange({ ...good, effectiveAt: "2027-03-02T00:00:00+03:00" });
    expect(r).toMatchObject({ effectiveAt: "2027-03-01T21:00:00.000Z" });
  });
  it("rollback and cancel take no value and no effective time", () => {
    expect(parseChange({ op: "rollback", key: good.key, reason: REASON, revision: 2 })).toEqual({
      op: "rollback",
      key: good.key,
      reason: REASON,
      revision: 2,
    });
    expect(
      parseChange({ op: "cancel", key: good.key, reason: REASON, revision: 2, value: 1 }),
    ).toMatchObject({
      status: 400,
    });
    expect(
      parseChange({
        op: "rollback",
        key: good.key,
        reason: REASON,
        revision: 2,
        effectiveAt: "2027-03-02T00:00:00Z",
      }),
    ).toMatchObject({ status: 400 });
  });
});

describe("runChange", () => {
  it("only the super admin (defence in depth; the route and the core also check)", async () => {
    for (const roles of [
      ["opsadmin"],
      ["curator"],
      ["safeguardinglead"],
      [],
    ] as AdminAccount["roles"][]) {
      const c = client();
      const r = await runChange({ admin: admin(roles), body: good, client: c });
      expect(r.status).toBe(403);
      expect(c.change).not.toHaveBeenCalled();
    }
    const c = client();
    expect((await runChange({ admin: admin(["superadmin"]), body: good, client: c })).status).toBe(
      200,
    );
    expect(c.change).toHaveBeenCalledWith(good);
  });
  it("an invalid body never reaches the core", async () => {
    const c = client();
    const r = await runChange({
      admin: admin(["superadmin"]),
      body: { ...good, actor_id: "x" },
      client: c,
    });
    expect(r.status).toBe(400);
    expect(c.change).not.toHaveBeenCalled();
  });
  it("passes the core's 4xx explanation through (validation text, current revision)", async () => {
    const c = client({
      change: async () => {
        throw new CoreSettingsError(409, "revision_conflict", "changed since", 4);
      },
    });
    const r = await runChange({ admin: admin(["superadmin"]), body: good, client: c });
    expect(r).toEqual({
      status: 409,
      body: { error: "revision_conflict", message: "changed since", currentRevision: 4 },
    });
    const c2 = client({
      change: async () => {
        throw new CoreSettingsError(422, "invalid_value", "must be between 1 and 720 hours");
      },
    });
    expect((await runChange({ admin: admin(["superadmin"]), body: good, client: c2 })).status).toBe(
      422,
    );
  });
  it("a 5xx, an unexpected error and a core 401 never leak details", async () => {
    for (const err of [
      new CoreSettingsError(500, "x", "secret stack"),
      new Error("boom: password=hunter2"),
    ]) {
      const c = client({ change: async () => Promise.reject(err) });
      const r = await runChange({ admin: admin(["superadmin"]), body: good, client: c });
      expect(r).toEqual({ status: 503, body: { error: "unavailable" } });
    }
    const c = client({
      change: async () => {
        throw new CoreSettingsError(401, "unauthenticated", "x");
      },
    });
    expect((await runChange({ admin: admin(["superadmin"]), body: good, client: c })).status).toBe(
      403,
    );
  });
});

describe("runRead", () => {
  const sa = admin(["superadmin"]);
  it("lists, or reads one history page", async () => {
    const c = client();
    expect((await runRead({ admin: sa, query: new URLSearchParams(), client: c })).status).toBe(
      200,
    );
    expect(c.list).toHaveBeenCalled();
    await runRead({
      admin: sa,
      query: new URLSearchParams("key=notify.rate_limit&before=5"),
      client: c,
    });
    expect(c.history).toHaveBeenCalledWith("notify.rate_limit", 5);
  });
  it.each([
    "key=../x",
    "key=A",
    "key=notify.rate_limit&before=0",
    "key=notify.rate_limit&before=x",
    "key=notify.rate_limit&before=-1",
    "key=notify.rate_limit&before=" + "9".repeat(12),
  ])("refuses %s", async (q) => {
    const c = client();
    const r = await runRead({ admin: sa, query: new URLSearchParams(q), client: c });
    expect(r.status).toBe(400);
    expect(c.history).not.toHaveBeenCalled();
  });
  it("non super admins are refused", async () => {
    const c = client();
    const r = await runRead({
      admin: admin(["opsadmin"]),
      query: new URLSearchParams(),
      client: c,
    });
    expect(r.status).toBe(403);
    expect(c.list).not.toHaveBeenCalled();
  });
});
