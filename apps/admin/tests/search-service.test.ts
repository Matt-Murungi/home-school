import { afterEach, describe, expect, it, vi } from "vitest";
import { ADMIN_ROLES, type AdminRole } from "@/lib/roles";
import type { AdminAccount } from "@/lib/auth/types";
import { FakeSearchSource } from "@/lib/search/fake";
import { MemoryAuditSink, MemoryRateLimiter } from "@/lib/search/limits";
import { runSearch, MAX_PER_GROUP } from "@/lib/search/service";
import { ENTITY_TYPES, type EntityType } from "@/lib/search/policy";
import type { RawHit } from "@/lib/search/types";

const admin = (...roles: AdminRole[]): AdminAccount => ({
  id: "admin1",
  name: "A",
  email: "a@x.test",
  roles,
});

const parent: RawHit = {
  type: "parent",
  id: "p1",
  fields: {
    name: "Grace Namuli",
    phone: "+256772123456",
    email: "grace@example.com",
    status: "active",
  },
};
const parentSG: RawHit = {
  type: "parent",
  id: "p2",
  linkedToCase: true,
  fields: { name: "Sarah Okello", phone: "+256701999888", email: "sarah@example.com" },
};
const teacher: RawHit = {
  type: "teacher",
  id: "t1",
  fields: {
    name: "Joseph Kato",
    phone: "+256782555111",
    email: "joseph@example.com",
    status: "approved",
  },
};
const child: RawHit = {
  type: "child",
  id: "c1",
  fields: {
    name: "Amina Namuli",
    class_level: "P4",
    date_of_birth: "2016-03-02",
    school: "Kampala Parents School",
    areas_of_concern: "stammer",
  },
};
const session: RawHit = {
  type: "session",
  id: "sess123abc",
  fields: {
    scheduled_start_at: "2026-11-25T11:00:00.000Z",
    status: "scheduled",
    teacher_name: "Joseph Kato",
    child_name: "Amina Namuli",
  },
};
const payment: RawHit = {
  type: "payment",
  id: "pay1",
  fields: {
    provider_reference: "MP240101.1234.A12345",
    amount: 45000,
    status: "confirmed",
    confirmed_at: "2026-11-24T08:00:00.000Z",
    secret_note: "internal-only",
  },
};
const topic: RawHit = {
  type: "topic",
  id: "top1",
  fields: { title: "Fractions", code: "MATH-P4-12" },
};
const question: RawHit = {
  type: "question",
  id: "q1",
  fields: { stem: "What is 1/2 + 1/4?", status: "approved" },
};
const sgCase: RawHit = {
  type: "case",
  id: "sgcase0007",
  fields: { category: "welfare", status: "open" },
};

const ALL = [parent, parentSG, teacher, child, session, payment, topic, question, sgCase];

function setup(
  records: RawHit[] = ALL,
  opts: ConstructorParameters<typeof FakeSearchSource>[1] = {},
) {
  const source = new FakeSearchSource(records, opts);
  const audit = new MemoryAuditSink();
  const limiter = new MemoryRateLimiter({ max: 1000, windowMs: 1000 }, () => 0);
  const deps = { source, audit, limiter };
  const search = async (a: AdminAccount, q: unknown) => runSearch({ admin: a, body: { q }, deps });
  return { source, audit, limiter, deps, search };
}
const typesOf = (r: Awaited<ReturnType<typeof runSearch>>) =>
  r.status === 200 ? r.body.groups.map((g) => g.type) : [];
const dump = (r: unknown) => JSON.stringify(r);

afterEach(() => vi.restoreAllMocks());

describe("acceptance: results grouped by type", () => {
  it("searching a MoMo reference finds its payment", async () => {
    const { search } = setup();
    const r = await search(admin("opsadmin"), "MP240101.1234.A12345");
    expect(r.status).toBe(200);
    if (r.status !== 200) return;
    expect(r.body.groups).toHaveLength(1);
    expect(r.body.groups[0]).toMatchObject({ type: "payment", label: "Payments" });
    expect(r.body.groups[0].hits[0]).toMatchObject({
      id: "pay1",
      title: "MP240101.1234.A12345",
      href: "/payments",
    });
    expect(r.body.groups[0].hits[0].subtitle).toMatch(/UGX 45,000/);
  });
  it("a partial reference and a lower-case reference also match", async () => {
    const { search } = setup();
    expect(typesOf(await search(admin("opsadmin"), "mp240101"))).toEqual(["payment"]);
    expect(typesOf(await search(admin("opsadmin"), "A12345"))).toEqual(["payment"]);
  });
  it("one query returns several groups in a fixed order", async () => {
    const { search } = setup();
    const r = await search(admin("opsadmin"), "namuli");
    expect(typesOf(r)).toEqual(["parent", "child", "session"]);
    const r2 = await search(admin("opsadmin"), "kato");
    expect(typesOf(r2)).toEqual(["teacher", "session"]);
  });
  it("empty groups are omitted and a no-match search is a 200 with no groups", async () => {
    const { search } = setup();
    const r = await search(admin("opsadmin"), "zzzzzz");
    expect(r).toMatchObject({ status: 200, body: { groups: [] } });
  });
});

describe("edge: partial phone numbers", () => {
  it.each(["0772123", "772 123", "+256772", "256772123456", "(0772) 123-456", "456"])(
    "%s finds the parent",
    async (q) => {
      const { search } = setup();
      const r = await search(admin("opsadmin"), q);
      expect(r.status === 200 && r.body.groups.some((g) => g.type === "parent")).toBe(true);
    },
  );
  it("two digits is too short; a non-matching fragment finds nothing", async () => {
    const { search } = setup();
    expect(await search(admin("opsadmin"), "07")).toMatchObject({ status: 400 });
    expect(await search(admin("opsadmin"), "0999888777")).toMatchObject({ body: { groups: [] } });
  });
});

describe("sessions by id, date, teacher and child", () => {
  it.each([
    ["sess123abc", "id"],
    ["2026-11-25", "ISO date"],
    ["25/11/2026", "day-first date"],
    ["Joseph", "teacher name"],
    ["Amina", "child name"],
  ])("%s (%s) finds the session", async (q) => {
    const { search } = setup();
    const r = await search(admin("opsadmin"), q);
    expect(r.status === 200 && r.body.groups.some((g) => g.type === "session")).toBe(true);
  });
  it("another day does not match; the date is shown in Africa/Kampala", async () => {
    const { search } = setup();
    expect(typesOf(await search(admin("opsadmin"), "2026-11-26"))).toEqual([]);
    const r = await search(admin("opsadmin"), "sess123abc");
    if (r.status !== 200) throw new Error();
    expect(r.body.groups[0].hits[0].subtitle).toMatch(/25 Nov 2026, 14:00/);
  });
});

describe("respects role permissions (server-side allowlist)", () => {
  it("superadmin alone: 403 and the data source is never touched", async () => {
    const { search, source, audit } = setup();
    expect(await search(admin("superadmin"), "namuli")).toMatchObject({ status: 403 });
    expect(await search(admin(), "namuli")).toMatchObject({ status: 403 });
    expect(source.calls).toEqual([]);
    expect(audit.entries).toEqual([]);
  });
  it("curator searching a phone number or a name gets nothing and people tables are not queried", async () => {
    const { search, source } = setup();
    for (const q of ["0772123", "+256772123456", "namuli", "grace@example.com", "MP240101"]) {
      const r = await search(admin("curator"), q);
      expect(r).toMatchObject({ status: 200, body: { groups: [] } });
    }
    for (const c of source.calls) expect(["topic", "question"]).toContain(c.type);
  });
  it("curator finds topics and questions", async () => {
    const { search } = setup();
    expect(typesOf(await search(admin("curator"), "fraction"))).toEqual(["topic"]);
    expect(typesOf(await search(admin("curator"), "1/2"))).toEqual(["question"]);
  });
  it("safeguarding lead: only case-linked people, cases, never payments", async () => {
    const { search, source } = setup();
    const r1 = await search(admin("safeguardinglead"), "namuli");
    expect(r1).toMatchObject({ status: 200, body: { groups: [] } });
    const r2 = await search(admin("safeguardinglead"), "okello");
    expect(typesOf(r2)).toEqual(["parent"]);
    expect(typesOf(await search(admin("safeguardinglead"), "sgcase"))).toEqual(["case"]);
    expect(typesOf(await search(admin("safeguardinglead"), "MP240101"))).toEqual([]);
    for (const c of source.calls) {
      expect(c.type).not.toBe("payment");
      if (["parent", "teacher", "child", "session"].includes(c.type))
        expect(c.scope).toBe("safeguarding");
    }
  });
  it("ops sees everything it may read but not catalogue or cases", async () => {
    const { search, source } = setup();
    await search(admin("opsadmin"), "fraction");
    for (const c of source.calls) expect(["topic", "question", "case"]).not.toContain(c.type);
    expect(typesOf(await search(admin("opsadmin"), "fraction"))).toEqual([]);
  });

  // The source is deliberately broken (ignores type and scope) to prove the service re-checks.
  it("a leaky data source cannot leak: results are re-checked per type and per scope", async () => {
    const { search } = setup(ALL, { leaky: true });
    const cur = await search(admin("curator"), "namuli");
    expect(dump(cur)).not.toMatch(/Namuli|namuli|Grace|256772/);
    const cur2 = await search(admin("curator"), "MP240101");
    expect(typesOf(cur2)).toEqual([]);
    const sg = await search(admin("safeguardinglead"), "namuli");
    expect(dump(sg)).not.toMatch(/Namuli|namuli|Grace|256772|Amina/);
    const sg2 = await search(admin("safeguardinglead"), "MP240101");
    expect(typesOf(sg2)).toEqual([]);
    const sg3 = await search(admin("safeguardinglead"), "fraction");
    expect(typesOf(sg3)).toEqual([]);
  });

  it("matrix over all 16 label subsets: every returned group is an allowed type", async () => {
    const queries = [
      "namuli",
      "kato",
      "MP240101",
      "fraction",
      "sgcase",
      "okello",
      "sess123abc",
      "1/2",
    ];
    for (let m = 0; m < 16; m++) {
      const roles = ADMIN_ROLES.filter((_, i) => m & (1 << i));
      const allowed = new Set<EntityType>();
      const has = (r: AdminRole) => roles.includes(r);
      if (has("opsadmin"))
        ["parent", "teacher", "child", "session", "payment"].forEach((t) =>
          allowed.add(t as EntityType),
        );
      if (has("curator")) ["topic", "question"].forEach((t) => allowed.add(t as EntityType));
      if (has("safeguardinglead"))
        ["parent", "teacher", "child", "session", "case"].forEach((t) =>
          allowed.add(t as EntityType),
        );
      for (const leaky of [false, true]) {
        const { search } = setup(ALL, { leaky });
        for (const q of queries) {
          const r = await search(admin(...roles), q);
          if (allowed.size === 0) {
            expect(r.status, roles.join("+")).toBe(403);
            continue;
          }
          expect(r.status).toBe(200);
          for (const t of typesOf(r))
            expect(allowed.has(t), `${roles.join("+")} ${q} ${t} leaky=${leaky}`).toBe(true);
          // the people/session/payment records only appear in full for ops
          if (!has("opsadmin") && r.status === 200) {
            for (const g of r.body.groups)
              if (["parent", "teacher", "child", "session"].includes(g.type))
                expect(
                  g.hits.every((h) => h.id === "p2"),
                  `${roles.join("+")} ${q}`,
                ).toBe(true);
          }
        }
      }
    }
    expect(ENTITY_TYPES.length).toBe(8);
  });
});

describe("privacy of what is returned", () => {
  it("opsadmin sees phone and email; safeguarding lead sees them masked", async () => {
    const { search } = setup();
    const ops = dump(await search(admin("opsadmin"), "grace"));
    expect(ops).toContain("+256772123456");
    expect(ops).toContain("grace@example.com");
    const sg = dump(await search(admin("safeguardinglead"), "okello"));
    expect(sg).not.toContain("+256701999888");
    expect(sg).not.toContain("sarah@example.com");
    expect(sg).toContain("+256•••••888");
    expect(sg).toContain("s•••@example.com");
  });
  it("a role that is not allowed the type never gets contact details", async () => {
    const { search } = setup(ALL, { leaky: true });
    expect(dump(await search(admin("curator"), "grace"))).not.toMatch(/256|example\.com/);
  });
  it("children are shown by first name and class only", async () => {
    const { search } = setup();
    const out = dump(await search(admin("opsadmin"), "amina namuli"));
    expect(out).toContain("Amina");
    expect(out).toContain("P4");
    expect(out).not.toMatch(/Kampala Parents|2016-03-02|stammer/);
    const r = await search(admin("opsadmin"), "amina namuli");
    if (r.status !== 200) throw new Error();
    const g = r.body.groups.find((x) => x.type === "child")!;
    expect(g.hits[0].title).toBe("Amina · P4");
  });
  it("only whitelisted fields leave the service", async () => {
    const { search } = setup();
    expect(dump(await search(admin("opsadmin"), "MP240101"))).not.toContain("internal-only");
  });
  it("hrefs are plain list routes: no query, no phone, no name in any URL", async () => {
    const { search } = setup();
    const r = await search(admin("opsadmin"), "0772123");
    expect(dump(r)).not.toMatch(/\?q=|772123"/);
    if (r.status !== 200) throw new Error();
    for (const g of r.body.groups) for (const h of g.hits) expect(h.href).toMatch(/^\/[a-z-]+$/);
  });
  it("nothing is written to the console while searching", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) =>
      vi.spyOn(console, m),
    );
    const { search } = setup(ALL, { failTypes: ["payment"] });
    await search(admin("opsadmin"), "0772123456");
    for (const s of spies) expect(s).not.toHaveBeenCalled();
  });
  it("the audit entry holds ids, roles, types and counts, never the query or any result text", async () => {
    const { search, audit } = setup();
    await search(admin("opsadmin"), "0772123456");
    expect(audit.entries).toHaveLength(1);
    const e = audit.entries[0];
    expect(e).toMatchObject({
      actorId: "admin1",
      action: "admin.search",
      roles: ["opsadmin"],
      queryKind: "phone",
    });
    expect(e.results.parent).toBe(1);
    const text = JSON.stringify(e);
    expect(text).not.toMatch(/0772|772123|Grace|example\.com/);
  });
});

describe("audit (SEC-6 / ADM-6): searches that touch personal data are logged", () => {
  it("logs ops and safeguarding searches, even with no hits; not curator catalogue searches", async () => {
    const { search, audit } = setup();
    await search(admin("opsadmin"), "zzzzzz");
    await search(admin("safeguardinglead"), "okello");
    await search(admin("curator"), "fraction");
    expect(audit.entries.map((e) => e.roles)).toEqual([["opsadmin"], ["safeguardinglead"]]);
  });
  it("rejected queries (too short, forbidden) are not logged as searches", async () => {
    const { search, audit } = setup();
    await search(admin("opsadmin"), "a");
    await search(admin("superadmin"), "grace");
    expect(audit.entries).toEqual([]);
  });
  it("fails closed: if the audit sink throws, no results are returned", async () => {
    const { deps } = setup();
    deps.audit.record = async () => {
      throw new Error("audit down: grace@example.com");
    };
    const r = await runSearch({ admin: admin("opsadmin"), body: { q: "grace" }, deps });
    expect(r.status).toBe(503);
    expect(dump(r)).not.toMatch(/Grace|grace@|audit down/);
  });
});

describe("limits, caps and failures", () => {
  const many = (n: number): RawHit[] =>
    Array.from({ length: n }, (_, i) => ({
      type: "payment" as const,
      id: `pay${i}`,
      fields: {
        provider_reference: `MP-${String(i).padStart(4, "0")}`,
        amount: 1000,
        status: "confirmed",
      },
    }));
  it("returns at most 20 per group, says there are more, and links to the list screen", async () => {
    const { search } = setup(many(60));
    const r = await search(admin("opsadmin"), "MP-");
    if (r.status !== 200) throw new Error();
    expect(MAX_PER_GROUP).toBe(20);
    expect(r.body.groups[0].hits).toHaveLength(20);
    expect(r.body.groups[0]).toMatchObject({ hasMore: true, seeAll: "/payments" });
  });
  it("exactly 20 matches is not 'more'", async () => {
    const { search } = setup(many(20));
    const r = await search(admin("opsadmin"), "MP-");
    if (r.status !== 200) throw new Error();
    expect(r.body.groups[0]).toMatchObject({ hasMore: false, seeAll: null });
  });
  it("asks the source for limit+1 rows and never uses an offset (Appwrite 5,000 cap)", async () => {
    const { search, source } = setup(many(60));
    await search(admin("opsadmin"), "MP-");
    expect(source.calls.length).toBeGreaterThan(0);
    for (const c of source.calls) {
      expect(c.limit).toBe(MAX_PER_GROUP + 1);
      expect(Object.keys(c)).not.toContain("offset");
    }
  });
  it("the see-all link is only given when the role may open that screen", async () => {
    const recs = Array.from({ length: 30 }, (_, i) => ({
      type: "case" as const,
      id: `sgcase${i}`,
      fields: { category: "welfare", status: "open" },
    }));
    const { search } = setup(recs);
    const r = await search(admin("safeguardinglead"), "sgcase");
    if (r.status !== 200) throw new Error();
    expect(r.body.groups[0].seeAll).toBe("/safeguarding");
  });
  it("rate limit: the 4th search in the window gets 429 with Retry-After and does not query", async () => {
    const { source, deps } = setup();
    deps.limiter = new MemoryRateLimiter({ max: 3, windowMs: 10_000 }, () => 0);
    for (let i = 0; i < 3; i++)
      expect(
        (await runSearch({ admin: admin("opsadmin"), body: { q: "grace" }, deps })).status,
      ).toBe(200);
    const n = source.calls.length;
    const r = await runSearch({ admin: admin("opsadmin"), body: { q: "grace" }, deps });
    expect(r).toMatchObject({ status: 429 });
    expect(r.status === 429 && r.retryAfterSeconds).toBeGreaterThan(0);
    expect(source.calls.length).toBe(n);
  });
  it("rate limit is per admin and recovers after the window", async () => {
    let t = 0;
    const lim = new MemoryRateLimiter({ max: 1, windowMs: 1000 }, () => t);
    expect(lim.check("a").allowed).toBe(true);
    expect(lim.check("a").allowed).toBe(false);
    expect(lim.check("b").allowed).toBe(true);
    t = 1001;
    expect(lim.check("a").allowed).toBe(true);
  });
  it("a body that is not {q: string} is a 400", async () => {
    const { deps } = setup();
    for (const body of [null, undefined, "x", 5, [], {}, { q: 5 }, { q: null }, { q: ["a"] }])
      expect(await runSearch({ admin: admin("opsadmin"), body, deps })).toMatchObject({
        status: 400,
      });
  });
  it("long input is a 400 and never reaches the source", async () => {
    const { search, source } = setup();
    expect(await search(admin("opsadmin"), "x".repeat(5000))).toMatchObject({ status: 400 });
    expect(source.calls).toEqual([]);
  });
  it("one failing type marks only that group unavailable; the rest still return", async () => {
    const { search } = setup(ALL, { failTypes: ["payment"] });
    const r = await search(admin("opsadmin"), "MP240101");
    if (r.status !== 200) throw new Error();
    expect(r.body.unavailable).toEqual(["payment"]);
    const r2 = await search(admin("opsadmin"), "grace");
    if (r2.status !== 200) throw new Error();
    expect(r2.body.groups.map((g) => g.type)).toContain("parent");
  });
  it("every type failing is a 503 with a generic body", async () => {
    const { search } = setup(ALL, { failTypes: [...ENTITY_TYPES] });
    const r = await search(admin("opsadmin"), "grace");
    expect(r).toMatchObject({ status: 503, body: { error: "unavailable" } });
    expect(dump(r)).not.toMatch(/boom|fake/i);
  });
  it("hostile text is just text: no error, nothing matched, source gets it verbatim", async () => {
    const { search, source } = setup();
    for (const q of [
      "'; DROP TABLE users;--",
      "<script>alert(1)</script>",
      "%%",
      "(.*)",
      "مريم",
      "‮grace",
    ]) {
      expect((await search(admin("opsadmin"), q)).status).toBe(200);
    }
    expect(source.calls.some((c) => c.query.text === "<script>alert(1)</script>")).toBe(true);
    expect(source.calls.every((c) => !c.query.text.includes("‮"))).toBe(true);
  });
});
