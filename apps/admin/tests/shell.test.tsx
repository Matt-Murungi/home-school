import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AdminShell } from "@/components/AdminShell";
import { EnvBadge } from "@/components/EnvBadge";

vi.mock("next/navigation", () => ({ usePathname: () => "/queues" }));

const admin = { name: "Amina K", email: "a@x.test", roles: ["opsadmin" as const] };

describe("layout shell", () => {
  it("shows the environment badge on every page via the shell", () => {
    render(
      <AdminShell envLabel="Staging" admin={admin}>
        <p>body</p>
      </AdminShell>,
    );
    expect(screen.getByTestId("env-badge")).toHaveTextContent("Staging");
  });
  it("Production badge renders distinctly", () => {
    render(<EnvBadge label="Production" />);
    expect(screen.getByTestId("env-badge")).toHaveAttribute("data-env", "production");
  });
  it("renders the sidebar for the role only, and marks the current page", () => {
    render(
      <AdminShell envLabel="Staging" admin={admin}>
        <p>body</p>
      </AdminShell>,
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Queues" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).queryByRole("link", { name: "Settings" })).toBeNull();
    expect(within(nav).getByRole("link", { name: "Payments" })).toBeInTheDocument();
  });
  it("shows the search placeholder, admin name and role", () => {
    render(
      <AdminShell envLabel="Staging" admin={admin}>
        <p>body</p>
      </AdminShell>,
    );
    expect(screen.getByRole("searchbox")).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/people, sessions, payments/i),
    );
    expect(screen.getByText("Amina K")).toBeInTheDocument();
    expect(screen.getByText("Operations admin")).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });
});
