import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { NAV_ITEMS } from "@/lib/nav";

const dir = join(__dirname, "../src/app/(admin)");

function pages(d: string, out: string[] = []): string[] {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    if (statSync(p).isDirectory()) pages(p, out);
    else if (f === "page.tsx") out.push(p);
  }
  return out;
}

// Layouts are skipped on client navigations and on RSC requests that already hold them, so the
// layout guard alone can be bypassed. Every page must call requireAccess with its own route.
describe("every admin page guards itself", () => {
  const files = pages(dir).filter((f) => !f.includes("/forbidden/"));
  it("covers one page per nav item", () => {
    expect(files).toHaveLength(NAV_ITEMS.length);
  });
  it.each(NAV_ITEMS.map((n) => n.href))(
    "page for %s calls requireAccess with its route",
    (href) => {
      const file = join(dir, href === "/" ? "" : href, "page.tsx");
      const text = readFileSync(file, "utf8");
      expect(text).toContain(`await requireAccess("${href}")`);
    },
  );
});
