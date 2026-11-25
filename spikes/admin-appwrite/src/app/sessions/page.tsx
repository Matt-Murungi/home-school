import Link from "next/link";
import { Query } from "node-appwrite";
import { DATABASE_ID, SESSIONS_TABLE_ID, STATUSES, tablesDB, type SessionRow } from "@/lib/appwrite";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 50;
const TOTAL_CAP = 5000;

type Search = { page?: string; status?: string };

export default async function SessionsPage({ searchParams }: { searchParams: Promise<Search> }) {
  const params = await searchParams;
  const page = Math.max(1, Number.parseInt(params.page ?? "1", 10) || 1);
  const status = STATUSES.find((s) => s === params.status);

  const queries = [
    Query.orderDesc("scheduled_at"),
    Query.limit(PAGE_SIZE),
    Query.offset((page - 1) * PAGE_SIZE),
  ];
  if (status) queries.push(Query.equal("status", status));

  const started = performance.now();
  const result = await tablesDB().listRows<SessionRow>({
    databaseId: DATABASE_ID,
    tableId: SESSIONS_TABLE_ID,
    queries,
  });
  const queryMs = Math.round(performance.now() - started);

  // Appwrite Cloud caps `total` at 5,000. At the cap the real count is unknown, so
  // "Next" depends on whether this page came back full, and there is no "Last".
  const capped = result.total >= TOTAL_CAP;
  const pages = capped ? null : Math.max(1, Math.ceil(result.total / PAGE_SIZE));
  const hasNext = capped ? result.rows.length === PAGE_SIZE : page < (pages ?? 1);
  const href = (p: number) => `/sessions?page=${p}${status ? `&status=${status}` : ""}`;

  return (
    <main>
      <header className="bar">
        <h1>Sessions</h1>
        <Link className="button" href="/sessions/new">New session</Link>
      </header>

      <form className="filters" method="get">
        <label>
          Status{" "}
          <select name="status" defaultValue={status ?? ""}>
            <option value="">All</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <button type="submit">Filter</button>
        <span className="muted" data-testid="timing">
          {capped ? `${TOTAL_CAP.toLocaleString()}+` : result.total.toLocaleString()} rows · page {page}
          {pages ? ` of ${pages}` : ""} · {result.rows.length} shown · query {queryMs} ms
        </span>
      </form>

      <table>
        <thead>
          <tr>
            <th>Scheduled</th><th>Child</th><th>Teacher</th><th>Subject</th><th>Status</th>
            <th className="num">Amount (UGX)</th>
          </tr>
        </thead>
        <tbody>
          {result.rows.map((row) => (
            <tr key={row.$id}>
              <td>{new Date(row.scheduled_at).toLocaleString("en-GB", { timeZone: "Africa/Kampala" })}</td>
              <td>{row.child_name}</td>
              <td>{row.teacher_name}</td>
              <td>{row.subject}</td>
              <td><span className={`pill ${row.status}`}>{row.status}</span></td>
              <td className="num">{row.amount_ugx.toLocaleString()}</td>
            </tr>
          ))}
          {result.rows.length === 0 && (
            <tr><td colSpan={6} className="muted">No sessions match.</td></tr>
          )}
        </tbody>
      </table>

      <nav className="pager">
        {page > 1 ? <Link href={href(1)}>« First</Link> : <span />}
        {page > 1 ? <Link href={href(page - 1)}>‹ Prev</Link> : <span />}
        <span>Page {page}{pages ? ` / ${pages}` : ""}</span>
        {hasNext ? <Link href={href(page + 1)}>Next ›</Link> : <span />}
        {pages && page < pages ? <Link href={href(pages)}>Last »</Link> : <span />}
      </nav>
    </main>
  );
}
