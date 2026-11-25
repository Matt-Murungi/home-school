/** Appwrite caps list `total` at 5,000 (ADR 0004). At the cap the real total is unknown. */
export const LIST_TOTAL_CAP = 5000;
export const DEFAULT_PAGE_SIZE = 25;
export const MAX_PAGE_SIZE = 100;

export function parsePageParams(p: { page?: string; pageSize?: string }) {
  const page = Math.max(1, Math.floor(Number(p.page)) || 1);
  const size = Math.floor(Number(p.pageSize)) || DEFAULT_PAGE_SIZE;
  const pageSize = Math.min(MAX_PAGE_SIZE, Math.max(1, size));
  return { page, pageSize, offset: (page - 1) * pageSize };
}

export function describePage(i: {
  total: number;
  page: number;
  pageSize: number;
  returned: number;
}) {
  const capped = i.total >= LIST_TOTAL_CAP;
  const pageCount = capped ? null : Math.max(1, Math.ceil(i.total / i.pageSize));
  const hasPrev = i.page > 1;
  const hasNext = capped ? i.returned === i.pageSize : i.page < (pageCount ?? 1);
  return {
    capped,
    totalLabel: capped ? "5,000+" : String(i.total),
    pageCount,
    hasPrev,
    hasNext,
    showLast: !capped && hasNext,
  };
}
