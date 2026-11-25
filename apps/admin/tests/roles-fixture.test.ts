import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { NAV_ITEMS } from "@/lib/nav";
import { ADMIN_ROLES, canAccessPath, rolesFromLabels, type AdminRole } from "@/lib/roles";

// Shared with the core service (A03, #45): services/core/tests/test_roles_model.py reads the same
// file. Change a row there and both suites must change, so F06/H09 and the core cannot drift.
type Fixture = {
  roles: string[];
  staff_roles: string[];
  apps: Record<string, string[]>;
  admin_routes: { href: string; roles: string[] }[];
  admin_path_cases: { path: string; roles: string[]; admits: boolean }[];
  label_cases: { labels: string[]; roles: string[] }[];
};
const fixture: Fixture = JSON.parse(
  readFileSync(path.resolve(__dirname, "../../../docs/ops/roles-matrix.json"), "utf8"),
);

const subsets = <T>(items: readonly T[]): T[][] =>
  items.reduce<T[][]>((acc, x) => acc.concat(acc.map((s) => [...s, x])), [[]]);

describe("shared roles fixture (docs/ops/roles-matrix.json)", () => {
  it("staff roles are exactly the admin roles", () => {
    expect([...ADMIN_ROLES]).toEqual(fixture.staff_roles);
    expect(fixture.apps.admin).toEqual(fixture.staff_roles);
  });

  it("the admin app admits only staff roles: none of parent, guardian, teacher", () => {
    for (const r of ["parent", "guardian", "teacher"]) {
      expect(fixture.apps.admin).not.toContain(r);
      expect(canAccessPath("/", [r as AdminRole])).toBe(false);
    }
  });

  it("label cases: exact alphanumeric labels only", () => {
    for (const c of fixture.label_cases) {
      const staff = c.roles.filter((r) => fixture.staff_roles.includes(r));
      expect([...rolesFromLabels(c.labels)].sort()).toEqual([...staff].sort());
    }
  });

  it("every nav route and guarded API route matches the fixture for every role subset", () => {
    const known = new Set(fixture.admin_routes.map((r) => r.href));
    for (const n of NAV_ITEMS) expect(known.has(n.href)).toBe(true);
    for (const r of fixture.admin_routes) {
      for (const subset of subsets(ADMIN_ROLES)) {
        const want = subset.some((x) => r.roles.includes(x));
        expect(canAccessPath(r.href, subset), `${r.href} ${subset.join(",")}`).toBe(want);
      }
    }
  });

  it("every fixture route is either a nav item or the one guarded API route", () => {
    const navHrefs = new Set(NAV_ITEMS.map((n) => n.href));
    const extra = fixture.admin_routes.filter((r) => !navHrefs.has(r.href)).map((r) => r.href);
    expect(extra).toEqual(["/api/search"]);
  });

  it("path cases (sub-paths, look-alike prefixes, unknown routes, no roles)", () => {
    for (const c of fixture.admin_path_cases) {
      const staff = c.roles.filter((r): r is AdminRole =>
        (fixture.staff_roles as string[]).includes(r),
      );
      expect(canAccessPath(c.path, staff), c.path).toBe(c.admits);
    }
  });
});
