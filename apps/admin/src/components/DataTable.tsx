import Link from "next/link";
import type { ReactNode } from "react";
import { describePage } from "@/lib/pagination";

export type Column<T> = {
  key: string;
  header: string;
  sortable?: boolean;
  render: (row: T) => ReactNode;
};

type Props<T> = {
  basePath: string;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Current URL query (filters, sort, dir): kept in every link. */
  query: Record<string, string>;
  total: number;
  page: number;
  pageSize: number;
  emptyMessage?: string;
};

function href(base: string, q: Record<string, string>, over: Record<string, string>) {
  const p = new URLSearchParams({ ...q, ...over });
  return `${base}?${p.toString()}`;
}

/** Server-rendered table: sorting, filters and paging are URL state handled by the server. */
export function DataTable<T>(props: Props<T>) {
  const { basePath, columns, rows, rowKey, query, total, page, pageSize } = props;
  const info = describePage({ total, page, pageSize, returned: rows.length });
  const go = (n: number) => href(basePath, query, { page: String(n) });
  return (
    <section>
      <div className="table-scroll" data-testid="table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((c) => {
                const active = query.sort === c.key;
                const dir = active ? query.dir : undefined;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    aria-sort={active ? (dir === "desc" ? "descending" : "ascending") : undefined}
                  >
                    {c.sortable ? (
                      <Link
                        href={href(basePath, query, {
                          sort: c.key,
                          dir: active && dir === "asc" ? "desc" : "asc",
                          page: "1",
                        })}
                      >
                        {c.header}
                      </Link>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length}>{props.emptyMessage ?? "Nothing to show"}</td>
              </tr>
            ) : (
              rows.map((r) => (
                <tr key={rowKey(r)}>
                  {columns.map((c) => (
                    <td key={c.key}>{c.render(r)}</td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <nav aria-label="Pagination" className="pager">
        {info.hasPrev && <Link href={go(page - 1)}>Previous</Link>}
        <span>
          Page {page}
          {info.pageCount ? ` of ${info.pageCount}` : ""} ·{" "}
          <span data-testid="total">{info.totalLabel}</span> total
        </span>
        {info.hasNext && <Link href={go(page + 1)}>Next</Link>}
        {info.showLast && info.pageCount && <Link href={go(info.pageCount)}>Last</Link>}
      </nav>
    </section>
  );
}
