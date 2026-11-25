// @vitest-environment node
import { describe, expect, it } from "vitest";
import { AppwriteSearchSource, type RowLister } from "@/lib/search/appwrite";
import { normalizeQuery } from "@/lib/search/normalize";
import type { QueryInfo, SourceRequest } from "@/lib/search/types";

// UNVERIFIED against a real project (needs the staging Appwrite, memory.md "Blocked on owner"):
// these tests only prove which tables and queries the adapter builds, against a recording stand-in.

type Call = {
  tableId: string;
  queries: { method: string; attribute?: string; values?: unknown[] }[];
};
function lister(
  tables: Record<string, Record<string, unknown>[] | ((c: Call) => Record<string, unknown>[])>,
) {
  const calls: Call[] = [];
  const l: RowLister = {
    async listRows({ databaseId, tableId, queries }) {
      expect(databaseId).toBe("main");
      const c: Call = { tableId, queries: queries.map((q) => JSON.parse(q)) };
      calls.push(c);
      const t = tables[tableId];
      return { rows: typeof t === "function" ? t(c) : (t ?? []) };
    },
  };
  return { calls, source: new AppwriteSearchSource({ databaseId: "main", rows: l }) };
}
const q = (raw: string): QueryInfo => {
  const r = normalizeQuery(raw);
  if (!r.ok) throw new Error(r.reason);
  return r.query;
};
const req = (
  type: SourceRequest["type"],
  raw: string,
  scope: SourceRequest["scope"] = "all",
): SourceRequest => ({
  type,
  query: q(raw),
  scope,
  limit: 21,
});
const has = (c: Call, method: string, attribute?: string) =>
  c.queries.some(
    (x) => x.method === method && (attribute === undefined || x.attribute === attribute),
  );
const flat = (c: Call) => JSON.stringify(c.queries);

describe("Appwrite search adapter (mock-tested, unverified)", () => {
  it("parents: matches name, phone fragment and email on user_profile, then keeps only users with a parent_profile", async () => {
    const { calls, source } = lister({
      user_profile: [
        { $id: "u1", name: "Grace", phone: "+256772123456", email: "g@x.test", status: "active" },
        { $id: "u2", name: "Gracious Teacher", phone: "+256772000000", email: "t@x.test" },
      ],
      parent_profile: [{ user_id: "u1" }],
    });
    const hits = await source.search(req("parent", "0772123"));
    expect(hits.map((h) => h.id)).toEqual(["u1"]);
    expect(hits[0].type).toBe("parent");
    expect(hits[0].fields.phone).toBe("+256772123456");
    const first = calls[0];
    expect(first.tableId).toBe("user_profile");
    expect(flat(first)).toContain("772123");
    expect(has(first, "or")).toBe(true);
    expect(has(first, "isNull", "deleted_at")).toBe(true);
    expect(calls[1].tableId).toBe("parent_profile");
  });
  it("never sends an offset and always caps the page at the requested limit", async () => {
    const { calls, source } = lister({
      user_profile: [],
      child: [],
      payment: [],
      topic: [],
      question: [],
    });
    for (const t of ["parent", "child", "payment", "topic", "question"] as const)
      await source.search(req(t, "grace"));
    for (const c of calls) {
      expect(has(c, "offset")).toBe(false);
      const lim = c.queries.find((x) => x.method === "limit");
      expect(lim?.values?.[0]).toBeLessThanOrEqual(c.tableId === "user_profile" ? 100 : 21);
    }
  });
  it("children: name contains, soft-deleted excluded, only the first-name fields are mapped", async () => {
    const { calls, source } = lister({
      child: [
        { $id: "c1", name: "Amina N", class_level: "P4", date_of_birth: "2016-03-02", school: "S" },
      ],
    });
    const hits = await source.search(req("child", "amina"));
    expect(calls[0].tableId).toBe("child");
    expect(has(calls[0], "contains", "name")).toBe(true);
    expect(has(calls[0], "isNull", "deleted_at")).toBe(true);
    expect(JSON.stringify(hits)).not.toMatch(/2016|"S"/);
  });
  it("payments: provider_reference (and exact id) lookup", async () => {
    const { calls, source } = lister({
      payment: [
        {
          $id: "pay1",
          provider_reference: "MP1",
          amount: 5000,
          status: "confirmed",
          confirmed_at: "2026-11-24T08:00:00.000Z",
          raw_callback: "SECRET",
        },
      ],
    });
    const hits = await source.search(req("payment", "MP240101"));
    expect(calls[0].tableId).toBe("payment");
    expect(has(calls[0], "contains", "provider_reference")).toBe(true);
    expect(JSON.stringify(hits)).not.toContain("SECRET");
    expect(hits[0].fields).toMatchObject({ provider_reference: "MP1", amount: 5000 });
  });
  it("sessions: id, Kampala-day range, and teacher/child name lookups", async () => {
    const { calls, source } = lister({
      session: [
        {
          $id: "s1",
          scheduled_start_at: "2026-11-25T11:00:00.000Z",
          status: "scheduled",
          child_id: "c1",
          teacher_id: "tp1",
        },
      ],
      child: [{ $id: "c1", name: "Amina N" }],
      teacher_profile: [{ $id: "tp1", user_id: "u9" }],
      user_profile: [{ $id: "u9", name: "Joseph Kato" }],
    });
    const hits = await source.search(req("session", "2026-11-25"));
    const s = calls.find((c) => c.tableId === "session")!;
    expect(flat(s)).toContain("2026-11-24T21:00:00.000Z");
    expect(has(s, "between", "scheduled_start_at")).toBe(true);
    expect(hits[0].fields).toMatchObject({ teacher_name: "Joseph Kato", child_name: "Amina N" });
    expect(has(s, "isNull", "deleted_at")).toBe(true);
  });
  it("catalogue: topic title/code and question stem", async () => {
    const { calls, source } = lister({
      topic: [{ $id: "t1", title: "Fractions", code: "M1" }],
      question: [{ $id: "q1", stem: "1/2?", status: "approved", answer_key: "SECRET" }],
    });
    const t = await source.search(req("topic", "fraction"));
    const qs = await source.search(req("question", "1/2"));
    expect(flat(calls[0])).toContain('"title"');
    expect(has(calls[1], "contains", "stem")).toBe(true);
    expect(JSON.stringify([t, qs])).not.toContain("SECRET");
  });
  it("safeguarding scope: people and sessions are restricted to ids from open cases; no cases = no query and no hits", async () => {
    const none = lister({ safeguarding_case: [], child: [{ $id: "c1", name: "Amina" }] });
    expect(await none.source.search(req("child", "amina", "safeguarding"))).toEqual([]);
    expect(none.calls.map((c) => c.tableId)).toEqual(["safeguarding_case"]);

    const some = lister({
      safeguarding_case: [{ $id: "k1", child_id: "c1", teacher_id: "tp1", session_id: "s1" }],
      child: [{ $id: "c1", name: "Amina", class_level: "P4" }],
    });
    const hits = await some.source.search(req("child", "amina", "safeguarding"));
    expect(hits[0].linkedToCase).toBe(true);
    const childCall = some.calls.find((c) => c.tableId === "child")!;
    expect(has(childCall, "equal", "$id")).toBe(true);
    const cc = some.calls[0];
    expect(has(cc, "notEqual", "status")).toBe(true);
  });
  it("cases: exact id or category", async () => {
    const { calls, source } = lister({
      safeguarding_case: [
        { $id: "k1", category: "welfare", status: "open", description: "PRIVATE" },
      ],
    });
    const hits = await source.search(req("case", "welfare"));
    expect(calls[0].tableId).toBe("safeguarding_case");
    expect(JSON.stringify(hits)).not.toContain("PRIVATE");
  });
  it("safeguarding scope for parents, teachers and sessions goes through the open cases", async () => {
    const t = lister({
      safeguarding_case: [{ $id: "k1", child_id: "c1", teacher_id: "tp1", session_id: "s1" }],
      teacher_profile: [{ $id: "tp1", user_id: "u9" }],
      user_profile: [{ $id: "u9", name: "Joseph Kato", phone: "+256782555111", email: "j@x.test" }],
    });
    const hits = await t.source.search(req("teacher", "kato", "safeguarding"));
    expect(hits.map((h) => h.id)).toEqual(["u9"]);
    expect(hits[0].linkedToCase).toBe(true);
    const up = t.calls.find((c) => c.tableId === "user_profile")!;
    expect(has(up, "equal", "$id")).toBe(true);

    const p = lister({
      safeguarding_case: [{ $id: "k1", child_id: "c1" }],
      child: [{ $id: "c1", parent_id: "pp1" }],
      parent_profile: [{ $id: "pp1", user_id: "u1" }],
      user_profile: [{ $id: "u1", name: "Grace" }],
    });
    expect(
      (await p.source.search(req("parent", "grace", "safeguarding"))).map((h) => h.id),
    ).toEqual(["u1"]);

    const s = lister({ safeguarding_case: [], session: [{ $id: "s1" }] });
    expect(await s.source.search(req("session", "2026-11-25", "safeguarding"))).toEqual([]);
    expect(s.calls.map((c) => c.tableId)).not.toContain("session");
  });
  it("hostile text is passed as a query value only, never interpolated", async () => {
    const { calls, source } = lister({ child: [] });
    await source.search(req("child", `x") OR 1=1 --`));
    expect(calls[0].queries.every((x) => typeof x.method === "string")).toBe(true);
    expect(flat(calls[0])).toContain('x\\") OR 1=1 --');
  });
  it("a store error propagates (the service turns it into 'unavailable')", async () => {
    const l: RowLister = {
      listRows: async () => {
        throw new Error("boom");
      },
    };
    const s = new AppwriteSearchSource({ databaseId: "main", rows: l });
    await expect(s.search(req("child", "amina"))).rejects.toThrow();
  });
});
