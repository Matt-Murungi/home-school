import { NAV_ITEMS } from "./nav";

/** Appwrite user labels are alphanumeric only (ADR 0012): SR role names without underscores. */
export const ADMIN_ROLES = ["opsadmin", "curator", "safeguardinglead", "superadmin"] as const;
export type AdminRole = (typeof ADMIN_ROLES)[number];

export const ROLE_NAMES: Record<AdminRole, string> = {
  opsadmin: "Operations admin",
  curator: "Curator",
  safeguardinglead: "Safeguarding lead",
  superadmin: "Super admin",
};

export function rolesFromLabels(labels: readonly string[]): AdminRole[] {
  return ADMIN_ROLES.filter((r) => labels.includes(r));
}

/** Longest-boundary match of a path against the nav routes; unknown routes are denied. */
export function canAccessPath(pathname: string, roles: readonly AdminRole[]): boolean {
  const item = [...NAV_ITEMS, ...API_ROUTES].find((n) => matches(n.href, pathname));
  return !!item && item.roles.some((r) => roles.includes(r));
}

/**
 * Guarded routes that are not sidebar screens. /api/search: the roles that may search anything
 * (kept equal to the per-type allowlist by tests/search-policy.test.ts).
 */
const API_ROUTES: readonly { href: string; roles: readonly AdminRole[] }[] = [
  { href: "/api/search", roles: ["opsadmin", "curator", "safeguardinglead"] },
  // H05: the A-50 settings screen's data route; super admin only, like the screen (NFR-18).
  { href: "/api/settings", roles: ["superadmin"] },
];

function matches(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}
