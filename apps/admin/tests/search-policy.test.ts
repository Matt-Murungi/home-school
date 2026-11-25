import { describe, expect, it } from "vitest";
import { NAV_ITEMS } from "@/lib/nav";
import { ADMIN_ROLES, canAccessPath, type AdminRole } from "@/lib/roles";
import {
  ENTITY_ROUTE,
  ENTITY_TYPES,
  canRevealContact,
  searchableTypes,
  type EntityType,
} from "@/lib/search/policy";

const subsets: AdminRole[][] = [];
for (let m = 0; m < 16; m++) subsets.push(ADMIN_ROLES.filter((_, i) => m & (1 << i)));

const types = (roles: AdminRole[]) => [...searchableTypes(roles).keys()].sort();

describe("per-role allowlist of searchable entity types (SR 6.2)", () => {
  it("opsadmin: parents, teachers, children, sessions, payments", () => {
    expect(types(["opsadmin"])).toEqual(["child", "parent", "payment", "session", "teacher"]);
  });
  it("curator: catalogue topics and questions only", () => {
    expect(types(["curator"])).toEqual(["question", "topic"]);
  });
  it("safeguardinglead: safeguarding-scoped people and sessions, plus cases, never payments", () => {
    const m = searchableTypes(["safeguardinglead"]);
    expect([...m.keys()].sort()).toEqual(["case", "child", "parent", "session", "teacher"]);
    expect(m.get("parent")).toBe("safeguarding");
    expect(m.get("child")).toBe("safeguarding");
    expect(m.get("session")).toBe("safeguarding");
    expect(m.get("case")).toBe("all");
    expect(m.has("payment")).toBe(false);
  });
  it("superadmin alone searches nothing (Overview and Settings only, F06 audit)", () => {
    expect(types(["superadmin"])).toEqual([]);
    expect(types([])).toEqual([]);
  });
  it("roles combine; the broader scope wins", () => {
    const m = searchableTypes(["opsadmin", "safeguardinglead"]);
    expect(m.get("parent")).toBe("all");
    expect(m.get("case")).toBe("all");
    expect(m.get("payment")).toBe("all");
  });

  it("matrix: all 16 label subsets x all entity types", () => {
    expect(subsets).toHaveLength(16);
    for (const roles of subsets) {
      const m = searchableTypes(roles);
      for (const t of ENTITY_TYPES) {
        const expected = (r: AdminRole, t: EntityType) =>
          (<Record<AdminRole, EntityType[]>>{
            opsadmin: ["parent", "teacher", "child", "session", "payment"],
            curator: ["topic", "question"],
            safeguardinglead: ["parent", "teacher", "child", "session", "case"],
            superadmin: [],
          })[r].includes(t);
        const should = roles.some((r) => expected(r, t));
        expect(m.has(t), `${roles.join("+") || "none"} / ${t}`).toBe(should);
      }
    }
  });

  it("scope is 'all' only when a role with full access to that type is held", () => {
    for (const roles of subsets) {
      const m = searchableTypes(roles);
      for (const [t, scope] of m) {
        const people: string[] = ["parent", "teacher", "child", "session"];
        const full = roles.includes("opsadmin") && people.includes(t);
        if (people.includes(t))
          expect(scope, `${roles.join("+")} / ${t}`).toBe(full ? "all" : "safeguarding");
      }
    }
  });

  it("every entity type has a list route, and every role allowed the type can open that route", () => {
    for (const t of ENTITY_TYPES) expect(NAV_ITEMS.map((n) => n.href)).toContain(ENTITY_ROUTE[t]);
    for (const roles of subsets)
      for (const t of searchableTypes(roles).keys())
        expect(canAccessPath(ENTITY_ROUTE[t], roles), `${roles.join("+")} / ${t}`).toBe(true);
  });

  it("the /api/search route is open to exactly the roles that can search something", () => {
    for (const r of ADMIN_ROLES)
      expect(canAccessPath("/api/search", [r]), r).toBe(searchableTypes([r]).size > 0);
    for (const roles of subsets)
      expect(canAccessPath("/api/search", roles)).toBe(searchableTypes(roles).size > 0);
  });
  it("/api/search is not a sidebar item", () => {
    expect(NAV_ITEMS.some((n) => n.href.startsWith("/api"))).toBe(false);
  });

  it("contact details are shown unmasked to opsadmin only", () => {
    expect(canRevealContact(["opsadmin"])).toBe(true);
    expect(canRevealContact(["safeguardinglead"])).toBe(false);
    expect(canRevealContact(["curator", "superadmin"])).toBe(false);
    expect(canRevealContact(["safeguardinglead", "opsadmin"])).toBe(true);
  });
});
