import { describe, expect, it } from "vitest";
import { LIST_TOTAL_CAP, describePage, parsePageParams } from "@/lib/pagination";

describe("pagination honours the 5,000 cap", () => {
  it("reports an exact total under the cap", () => {
    const p = describePage({ total: 120, page: 2, pageSize: 25, returned: 25 });
    expect(p.totalLabel).toBe("120");
    expect(p.hasPrev).toBe(true);
    expect(p.hasNext).toBe(true);
    expect(p.showLast).toBe(true);
    expect(p.pageCount).toBe(5);
  });
  it("shows 5,000+ at the cap, with next/prev only", () => {
    const p = describePage({ total: LIST_TOTAL_CAP, page: 3, pageSize: 25, returned: 25 });
    expect(p.totalLabel).toBe("5,000+");
    expect(p.showLast).toBe(false);
    expect(p.pageCount).toBeNull();
    expect(p.hasNext).toBe(true);
    expect(p.hasPrev).toBe(true);
  });
  it("at the cap, a short page ends paging", () => {
    expect(describePage({ total: 5000, page: 200, pageSize: 25, returned: 10 }).hasNext).toBe(
      false,
    );
  });
  it("first page has no previous; empty result has no next", () => {
    expect(describePage({ total: 0, page: 1, pageSize: 25, returned: 0 })).toMatchObject({
      hasPrev: false,
      hasNext: false,
      totalLabel: "0",
    });
  });
  it("last exact page under the cap has no next", () => {
    expect(describePage({ total: 50, page: 2, pageSize: 25, returned: 25 }).hasNext).toBe(false);
  });
  it("parses and clamps page params", () => {
    expect(parsePageParams({ page: "abc" })).toEqual({ page: 1, pageSize: 25, offset: 0 });
    expect(parsePageParams({ page: "-4" }).page).toBe(1);
    expect(parsePageParams({ page: "3", pageSize: "1000" })).toEqual({
      page: 3,
      pageSize: 100,
      offset: 200,
    });
    expect(parsePageParams({ page: "3", pageSize: "10" }).offset).toBe(20);
  });
});
