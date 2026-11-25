import type { AdminRole } from "./roles";

export type NavItem = { label: string; href: string; roles: readonly AdminRole[]; spec: string };

const ALL: readonly AdminRole[] = ["opsadmin", "curator", "safeguardinglead", "superadmin"];
// SR 6.2: ops read the catalogue and questions; curators read/write.
const CATALOGUE: readonly AdminRole[] = ["opsadmin", "curator"];
const OPS: readonly AdminRole[] = ["opsadmin"];
const OPS_SG: readonly AdminRole[] = ["opsadmin", "safeguardinglead"];

// Order and names: Design Brief "Admin dashboard - left sidebar". Role visibility follows SR 6.2
// where it speaks (families, teachers, sessions, payments, catalogue, safeguarding, audit log).
// super_admin is NOT implicit: SR 6.2 has no super_admin column, A-30 says "safeguarding roles
// only", and the core service refuses superadmin on ops/safeguarding data (F03
// `test_superadmin_alone_does_not_pass_opsadmin_guard`). A user may hold several admin roles
// (SR 6.1), so a super admin who needs payments also holds opsadmin. Showing screens whose data
// calls would be refused is worse than hiding them.
// UNCONFIRMED guesses, to confirm with the owner: Overview (all), Queues (ops + safeguarding),
// Matching, Payouts, Reports (ops), Settings (superadmin only, from PRD "super admin configures
// plans, rates, tiers and platform settings" and NFR-18), and superadmin seeing only Overview and
// Settings. A-51 Admin users and roles has no sidebar item yet.
export const NAV_ITEMS: readonly NavItem[] = [
  { label: "Overview", href: "/", roles: ALL, spec: "A-03" },
  {
    label: "Queues",
    href: "/queues",
    roles: OPS_SG,
    spec: "A-02",
  },
  {
    label: "Teachers",
    href: "/teachers",
    roles: OPS_SG,
    spec: "A-06",
  },
  {
    label: "Families",
    href: "/families",
    roles: OPS_SG,
    spec: "A-08",
  },
  { label: "Matching", href: "/matching", roles: OPS, spec: "A-10" },
  {
    label: "Sessions",
    href: "/sessions",
    roles: OPS_SG,
    spec: "A-13",
  },
  { label: "Payments", href: "/payments", roles: OPS, spec: "A-20" },
  { label: "Payouts", href: "/payouts", roles: OPS, spec: "A-21" },
  {
    label: "Safeguarding",
    href: "/safeguarding",
    roles: ["safeguardinglead"],
    spec: "A-30",
  },
  { label: "Curriculum", href: "/curriculum", roles: CATALOGUE, spec: "A-40" },
  {
    label: "Question bank",
    href: "/question-bank",
    roles: CATALOGUE,
    spec: "A-41",
  },
  { label: "Reports", href: "/reports", roles: OPS, spec: "A-52" },
  { label: "Settings", href: "/settings", roles: ["superadmin"], spec: "A-50" },
  {
    label: "Audit log",
    href: "/audit-log",
    roles: OPS_SG,
    spec: "A-53",
  },
];

export function navFor(roles: readonly AdminRole[]): NavItem[] {
  return NAV_ITEMS.filter((n) => n.roles.some((r) => roles.includes(r)));
}
