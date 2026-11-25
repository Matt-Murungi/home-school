import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataTable } from "@/components/DataTable";

type Row = { id: string; name: string };
const columns = [{ key: "name", header: "Name", sortable: true, render: (r: Row) => r.name }];
const rows: Row[] = [{ id: "1", name: "Ann" }, { id: "2", name: "Ben" }];
const base = { basePath: "/teachers", columns, rows, rowKey: (r: Row) => r.id, query: {} as Record<string, string> };

describe("DataTable", () => {
  it("renders rows and exact total under the cap", () => {
    render(<DataTable {...base} total={2} page={1} pageSize={25} />);
    expect(screen.getByText("Ann")).toBeInTheDocument();
    expect(screen.getByTestId("total")).toHaveTextContent("2");
  });
  it("shows 5,000+ and only Previous/Next at the cap", () => {
    render(<DataTable {...base} total={5000} page={4} pageSize={2} />);
    expect(screen.getByTestId("total")).toHaveTextContent("5,000+");
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute("href", "/teachers?page=5");
    expect(screen.getByRole("link", { name: "Previous" })).toHaveAttribute("href", "/teachers?page=3");
    expect(screen.queryByRole("link", { name: "Last" })).toBeNull();
  });
  it("shows Last when the total is exact", () => {
    render(<DataTable {...base} total={10} page={1} pageSize={2} />);
    expect(screen.getByRole("link", { name: "Last" })).toHaveAttribute("href", "/teachers?page=5");
  });
  it("keeps filters in pagination links and toggles sort", () => {
    render(<DataTable {...base} query={{ status: "open", sort: "name", dir: "asc" }} total={100} page={1} pageSize={2} />);
    expect(screen.getByRole("link", { name: "Next" })).toHaveAttribute("href", expect.stringContaining("status=open"));
    expect(screen.getByRole("link", { name: /Name/ })).toHaveAttribute("href", expect.stringContaining("dir=desc"));
    expect(screen.getByRole("columnheader", { name: /Name/ })).toHaveAttribute("aria-sort", "ascending");
  });
  it("shows an empty state and disables Next on a short page", () => {
    render(<DataTable {...base} rows={[]} total={0} page={1} pageSize={25} emptyMessage="No teachers match" />);
    expect(screen.getByText("No teachers match")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Next" })).toBeNull();
  });
  it("scrolls horizontally inside its own box (narrow window)", () => {
    render(<DataTable {...base} total={2} page={1} pageSize={25} />);
    expect(screen.getByTestId("table-scroll").className).toContain("table-scroll");
  });
});
