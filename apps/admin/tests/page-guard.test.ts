import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { NAV_ITEMS } from "@/lib/nav";
import { scanApp, type Allowlist } from "./helpers/guard-scan";

// Layouts are skipped on client navigations and on RSC requests that already hold them, and do not
// wrap `api/` or routes outside `(admin)`, so the layout guard alone can be bypassed. Every page,
// route handler, server action and layout must itself call requireAccess. This test walks ALL of
// src/app (not only (admin)) and treats only a real top-level `await requireAccess(...)` as a guard.

/** Intentionally public (or self-guarding) routes. Every entry must say why. */
export const PUBLIC_ROUTES: Allowlist = {
  "layout.tsx": "Root layout: html shell only, renders no data and no session information.",
  "login/page.tsx":
    "Sign-in page: must be reachable without a session (middleware sends a signed-in admin away).",
  "(admin)/forbidden/page.tsx":
    "Static 'no access' page the guard redirects to; sits inside (admin) so the layout still requires an admin session, shows no data.",
  "api/session/route.ts":
    "Login, MFA and logout form POSTs: they establish the session so cannot require one; same-origin checked and form-only.",
  "api/session/clear/route.ts":
    "Internal redirect target for an invalid session: validates the session itself and only clears it when it is not a valid admin.",
};

const SRC = join(__dirname, "../src");

describe("the real app: every page, route, layout and server action is guarded", () => {
  const { violations, checked } = scanApp(SRC, PUBLIC_ROUTES);
  it("has no unguarded entry point", () => {
    expect(violations).toEqual([]);
  });
  it("sees one page per nav item plus the allow-listed files", () => {
    const pages = checked.filter((f) => f.startsWith("(admin)/") && f.endsWith("/page.tsx"));
    // one per nav item (overview is (admin)/page.tsx) plus the allow-listed forbidden page
    expect(pages).toHaveLength(NAV_ITEMS.length + 1);
    for (const k of Object.keys(PUBLIC_ROUTES)) expect(checked).toContain(k);
  });
});

// ---- proof that the checker fails for each bypass the audit found -------------------------------

const GOOD_PAGE = `import { requireAccess } from "@/lib/session";
export default async function P() { await requireAccess("/zzz"); return null; }`;

let tmp: string[] = [];
afterEach(() => {
  for (const d of tmp) rmSync(d, { recursive: true, force: true });
  tmp = [];
});
function tree(files: Record<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), "guard-"));
  tmp.push(root);
  for (const [f, text] of Object.entries(files)) {
    const p = join(root, f);
    mkdirSync(dirname(p), { recursive: true });
    writeFileSync(p, text);
  }
  return root;
}
const scan = (files: Record<string, string>, allow: Allowlist = {}) =>
  scanApp(tree(files), allow).violations;

describe("guard checker fails closed", () => {
  it("passes a guarded page", () => {
    expect(scan({ "app/zzz/page.tsx": GOOD_PAGE })).toEqual([]);
  });
  it("unguarded page OUTSIDE (admin)", () => {
    const v = scan({ "app/zzz/page.tsx": `export default function P() { return null; }` });
    expect(v).toHaveLength(1);
    expect(v[0]).toMatchObject({ file: "zzz/page.tsx", kind: "page" });
  });
  it("unguarded route.ts INSIDE (admin)", () => {
    const v = scan({
      "app/(admin)/x/route.ts": `export async function GET() { return new Response("data"); }`,
    });
    expect(v).toHaveLength(1);
    expect(v[0]).toMatchObject({ file: "(admin)/x/route.ts", kind: "route" });
  });
  it("unguarded route.ts under api/", () => {
    const v = scan({
      "app/api/export/route.ts": `export const POST = async () => new Response("data");`,
    });
    expect(v).toHaveLength(1);
    expect(v[0]).toMatchObject({ file: "api/export/route.ts", kind: "route" });
  });
  it("a route guarding only one of two methods is flagged for the other", () => {
    const v = scan({
      "app/api/x/route.ts": `import { requireAccess } from "@/lib/session";
export async function GET() { await requireAccess("/api/x"); return new Response(""); }
export async function DELETE() { return new Response(""); }`,
    });
    expect(v.map((x) => x.problem)).toEqual([expect.stringMatching(/^DELETE:/)]);
  });
  it("requireAccess only in a COMMENT does not count", () => {
    const v = scan({
      "app/zzz/page.tsx": `// await requireAccess("/zzz");
/* await requireAccess("/zzz") */
export default function P() { return null; }`,
    });
    expect(v).toHaveLength(1);
  });
  it("requireAccess only in a STRING does not count", () => {
    const v = scan({
      "app/zzz/page.tsx": `export default function P() { return "await requireAccess('/zzz')"; }`,
    });
    expect(v).toHaveLength(1);
  });
  it("a conditional or un-awaited call does not count", () => {
    const cond = `import { requireAccess } from "@/lib/session";
export default async function P() { if (Math.random() > 1) await requireAccess("/zzz"); return null; }`;
    const noAwait = `import { requireAccess } from "@/lib/session";
export default async function P() { requireAccess("/zzz"); return null; }`;
    expect(scan({ "app/zzz/page.tsx": cond })).toHaveLength(1);
    expect(scan({ "app/zzz/page.tsx": noAwait })).toHaveLength(1);
  });
  it("a guard naming a different route is flagged", () => {
    const v = scan({ "app/(admin)/payments/page.tsx": GOOD_PAGE.replace("/zzz", "/reports") });
    expect(v[0].problem).toMatch(/does not match/);
  });
  it("a guarded dynamic page may use its static prefix", () => {
    const v = scan({
      "app/(admin)/teachers/[id]/page.tsx": GOOD_PAGE.replace("/zzz", "/teachers"),
    });
    expect(v).toEqual([]);
  });
  it("a @slot page and default.tsx are enumerated too", () => {
    const v = scan({
      "app/(admin)/@modal/page.tsx": `export default function P() { return null; }`,
      "app/(admin)/@modal/default.tsx": `export default function D() { return null; }`,
    });
    expect(v.map((x) => x.kind).sort()).toEqual(["default", "page"]);
  });
  it("an unguarded layout is flagged; a layout using currentAdmin passes", () => {
    const bad = scan({
      "app/(admin)/layout.tsx": `export default function L({children}: any) { return children; }`,
    });
    expect(bad).toHaveLength(1);
    const ok = scan({
      "app/(admin)/layout.tsx": `import { currentAdmin } from "@/lib/session";
export default async function L({children}: any) { const a = await currentAdmin(); return children; }`,
    });
    expect(ok).toEqual([]);
  });
  it("an unguarded server action file and an inline server action are flagged", () => {
    const file = scan({
      "app/actions.ts": `"use server";
export async function save() { return 1; }`,
    });
    expect(file).toHaveLength(1);
    expect(file[0].kind).toBe("server-action");
    const inline = scan({
      "app/zzz/page.tsx": `import { requireAccess } from "@/lib/session";
export default async function P() { await requireAccess("/zzz");
  async function save() { "use server"; return 1; }
  return null; }`,
    });
    expect(inline).toHaveLength(1);
    const guarded = scan({
      "lib/actions.ts": `"use server";
import { requireAccess } from "@/lib/session";
export async function save() { await requireAccess("/teachers"); return 1; }`,
    });
    expect(guarded).toEqual([]);
  });
  it("allowlist entries need a reason and must not be stale", () => {
    const files = { "app/login/page.tsx": `export default function P() { return null; }` };
    expect(scan(files, { "login/page.tsx": "ok" })[0].kind).toBe("allowlist");
    expect(scan(files, { "login/page.tsx": "Public sign-in page, no data rendered." })).toEqual([]);
    const stale = scan(files, {
      "login/page.tsx": "Public sign-in page, no data rendered.",
      "gone/page.tsx": "Used to exist and was public once upon a time.",
    });
    expect(stale.map((x) => x.problem)).toEqual([expect.stringMatching(/stale/)]);
  });
});
