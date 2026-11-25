import { describe, expect, it } from "vitest";
import { NAV_ITEMS, navFor } from "@/lib/nav";
import { ADMIN_ROLES, canAccessPath, rolesFromLabels } from "@/lib/roles";

describe("roles from Appwrite labels (alphanumeric, ADR 0012)", () => {
  it("maps the four labels and ignores everything else", () => {
    expect(rolesFromLabels(["opsadmin", "parent", "superadmin"])).toEqual([
      "opsadmin",
      "superadmin",
    ]);
    expect(rolesFromLabels(["ops_admin", "OpsAdmin"])).toEqual([]);
    expect(rolesFromLabels([])).toEqual([]);
  });
  it("lists exactly the four admin roles", () => {
    expect([...ADMIN_ROLES].sort()).toEqual([
      "curator",
      "opsadmin",
      "safeguardinglead",
      "superadmin",
    ]);
  });
});

describe("sidebar navigation (Design Brief, admin left sidebar)", () => {
  it("has the 14 mockup items in order", () => {
    expect(NAV_ITEMS.map((n) => n.label)).toEqual([
      "Overview",
      "Queues",
      "Teachers",
      "Families",
      "Matching",
      "Sessions",
      "Payments",
      "Payouts",
      "Safeguarding",
      "Curriculum",
      "Question bank",
      "Reports",
      "Settings",
      "Audit log",
    ]);
  });
  it("every item has a unique route", () => {
    const hrefs = NAV_ITEMS.map((n) => n.href);
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });
  it("superadmin is NOT implicit: sees only overview and settings (SR 6.2 has no super_admin column; F03 core refuses it)", () => {
    expect(navFor(["superadmin"]).map((n) => n.label)).toEqual(["Overview", "Settings"]);
  });
  it("superadmin who also holds a role sees that role's items", () => {
    const labels = navFor(["superadmin", "safeguardinglead"]).map((n) => n.label);
    expect(labels).toContain("Safeguarding");
    expect(labels).not.toContain("Payments");
  });
  it("safeguarding cases are for safeguarding roles only (A-30)", () => {
    expect(navFor(["opsadmin", "curator", "superadmin"]).map((n) => n.label)).not.toContain(
      "Safeguarding",
    );
  });
  it("curator sees catalogue, not payments or safeguarding", () => {
    const labels = navFor(["curator"]).map((n) => n.label);
    expect(labels).toContain("Curriculum");
    expect(labels).toContain("Question bank");
    expect(labels).not.toContain("Payments");
    expect(labels).not.toContain("Safeguarding");
  });
});

describe("route guard by label", () => {
  const cases: Array<[string, string[], boolean]> = [
    ["/payments", ["opsadmin"], true],
    ["/payments", ["curator"], false],
    ["/payments", ["safeguardinglead"], false],
    ["/payments", ["superadmin"], false], // SR 6.2: payments are ops only
    ["/safeguarding", ["safeguardinglead"], true],
    ["/safeguarding", ["opsadmin"], false],
    ["/safeguarding", ["superadmin"], false], // A-30: safeguarding roles only
    ["/safeguarding", ["superadmin", "safeguardinglead"], true],
    ["/curriculum", ["curator"], true],
    ["/curriculum/42", ["curator"], true],
    ["/curriculum", ["opsadmin"], true], // SR 6.2: ops read the catalogue
    ["/curriculum", ["safeguardinglead"], false],
    ["/audit-log", ["opsadmin"], true], // SR 6.2: ops and safeguarding read the audit log
    ["/audit-log", ["safeguardinglead"], true],
    ["/audit-log", ["curator"], false],
    ["/payments", ["ops_admin"], false],
    ["/payments", ["OpsAdmin"], false],
    ["/settings", ["superadmin"], true],
    ["/settings", ["opsadmin"], false],
    ["/", ["curator"], true],
    ["/payments", [], false],
    ["/payments", ["opsadmin", "curator"], true],
    ["/unknown-route", ["opsadmin"], false],
  ];
  it.each(cases)("%s with %j -> %s", (path, roles, allowed) => {
    expect(canAccessPath(path, rolesFromLabels(roles))).toBe(allowed);
  });
  it("does not match on a path prefix without a boundary", () => {
    expect(canAccessPath("/paymentsx", ["opsadmin"])).toBe(false);
  });
});
