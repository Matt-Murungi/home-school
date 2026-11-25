import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsEditor } from "@/components/settings/SettingsEditor";
import type { SettingsList, SettingView } from "@/lib/settings/types";
import { INT, WEIGHTS } from "./helpers/settings-fixtures";

const LATE: SettingView = {
  ...INT,
  key: "session.late_cancellation_notice_hours",
  group: "business_rules",
  applies: "next_day",
  unit: "hours",
  min: 1,
  max: 168,
  value: 24,
  default: 24,
};
const AUTO: SettingView = { ...INT, group: "business_rules", value: 48, default: 48 };
const W: SettingView = { ...WEIGHTS, group: "matching" };
const initial = (): SettingsList => ({
  groups: [
    { id: "business_rules", title: "Business rules" },
    { id: "matching", title: "Matching" },
    { id: "travel", title: "Travel time" },
  ],
  settings: [AUTO, LATE, W],
});
const REASON = "Owner decision recorded in the meeting";

let fetchMock: ReturnType<typeof vi.fn>;
const respond = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
beforeEach(() => {
  fetchMock = vi.fn(async () => respond(200, {}));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

const posted = () =>
  JSON.parse(String((fetchMock.mock.calls.at(-1) as [string, RequestInit])[1].body));

describe("A-50 settings screen", () => {
  it("lists settings by group with current value, default, source and when it takes effect", () => {
    render(<SettingsEditor initial={initial()} />);
    expect(screen.getByRole("heading", { name: "Business rules" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Matching" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Travel time" })).toBeNull(); // empty group hidden
    const card = screen.getByRole("article", { name: LATE.key });
    expect(within(card).getByTestId(`current-${LATE.key}`)).toHaveTextContent("24 hours");
    expect(card).toHaveTextContent(/next Kampala midnight/);
    expect(screen.getByRole("article", { name: AUTO.key })).toHaveTextContent(/Immediately/);
  });

  it("shows current vs new, validates the number, and needs a reason before it sends anything", async () => {
    const u = userEvent.setup();
    render(<SettingsEditor initial={initial()} />);
    await u.click(screen.getByRole("button", { name: `Edit ${AUTO.key}` }));
    const input = screen.getByLabelText(/New value/);
    await u.clear(input);
    await u.type(input, "-5");
    expect(screen.getByText(/not valid yet/)).toBeInTheDocument();
    await u.type(screen.getByLabelText(/reason for change/i), "too short");
    await u.click(screen.getByRole("button", { name: "Save change" }));
    expect(
      screen
        .getAllByRole("alert")
        .map((a) => a.textContent)
        .join(" "),
    ).toMatch(/between 1 and 720/);
    expect(fetchMock).not.toHaveBeenCalled();

    await u.clear(input);
    await u.type(input, "24");
    expect(screen.getByText(/Current:/).textContent).toMatch(/48 hours.*New:.*24 hours/);
    await u.clear(screen.getByLabelText(/reason for change/i));
    await u.click(screen.getByRole("button", { name: "Save change" }));
    expect(screen.getByText(/reason of at least 10 characters/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an unchanged value without a request", async () => {
    const u = userEvent.setup();
    render(<SettingsEditor initial={initial()} />);
    await u.click(screen.getByRole("button", { name: `Edit ${AUTO.key}` }));
    await u.type(screen.getByLabelText(/reason for change/i), REASON);
    await u.click(screen.getByRole("button", { name: "Save change" }));
    expect(screen.getByText(/same as the current value/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends exactly op, key, value, reason and the revision it showed; then shows the new value", async () => {
    const u = userEvent.setup();
    fetchMock.mockResolvedValueOnce(
      respond(200, { ...AUTO, value: 24, revision: 1, source: "override" }),
    );
    render(<SettingsEditor initial={initial()} />);
    await u.click(screen.getByRole("button", { name: `Edit ${AUTO.key}` }));
    const input = screen.getByLabelText(/New value/);
    await u.clear(input);
    await u.type(input, "24");
    await u.type(screen.getByLabelText(/reason for change/i), REASON);
    await u.click(screen.getByRole("button", { name: "Save change" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/is now 24 hours/));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/settings");
    expect(init.method).toBe("POST");
    expect(posted()).toEqual({ op: "set", key: AUTO.key, value: 24, reason: REASON, revision: 0 });
    expect(screen.getByTestId(`current-${AUTO.key}`)).toHaveTextContent("24 hours");
    expect(screen.getByRole("article", { name: AUTO.key })).toHaveTextContent(/revision 1/);
  });

  it("an effective date is sent as a UTC instant (Kampala is UTC+3) and a past date is refused", async () => {
    const u = userEvent.setup();
    fetchMock.mockResolvedValueOnce(
      respond(200, {
        ...LATE,
        value: 24,
        pending: { revision: 1, value: 12, effective_at: "2099-03-01T21:00:00.000Z" },
        revision: 1,
      }),
    );
    render(<SettingsEditor initial={initial()} />);
    await u.click(screen.getByRole("button", { name: `Edit ${LATE.key}` }));
    const input = screen.getByLabelText(/New value/);
    await u.clear(input);
    await u.type(input, "12");
    await u.type(screen.getByLabelText(/reason for change/i), REASON);
    await u.type(screen.getByLabelText(/Effective from/), "2000-01-01T00:00");
    await u.click(screen.getByRole("button", { name: "Save change" }));
    expect(screen.getByText(/cannot take effect in the past/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    await u.clear(screen.getByLabelText(/Effective from/));
    await u.type(screen.getByLabelText(/Effective from/), "2099-03-02T00:00");
    await u.click(screen.getByRole("button", { name: "Save change" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(posted().effectiveAt).toBe("2099-03-01T21:00:00.000Z");
    await waitFor(() =>
      expect(screen.getByRole("note")).toHaveTextContent(
        /Scheduled: 12 hours from 2099-03-02 00:00/,
      ),
    );
  });

  it("weights are edited together and must add up to 100", async () => {
    const u = userEvent.setup();
    render(<SettingsEditor initial={initial()} />);
    await u.click(screen.getByRole("button", { name: "Edit matching.weights" }));
    const tier = screen.getByLabelText(/^tier/);
    await u.clear(tier);
    await u.type(tier, "21");
    expect(screen.getByText(/Current:/).textContent).toMatch(/not valid yet/);
    await u.type(screen.getByLabelText(/reason for change/i), REASON);
    await u.click(screen.getByRole("button", { name: "Save change" }));
    expect(screen.getByText(/add up to 100 \(now 101\)/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a stale revision (409) explains it and offers a reload that refreshes the list", async () => {
    const u = userEvent.setup();
    fetchMock.mockResolvedValueOnce(
      respond(409, { error: "revision_conflict", currentRevision: 3 }),
    );
    render(<SettingsEditor initial={initial()} />);
    await u.click(screen.getByRole("button", { name: `Edit ${AUTO.key}` }));
    const input = screen.getByLabelText(/New value/);
    await u.clear(input);
    await u.type(input, "24");
    await u.type(screen.getByLabelText(/reason for change/i), REASON);
    await u.click(screen.getByRole("button", { name: "Save change" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/changed by someone else/),
    );
    fetchMock.mockResolvedValueOnce(
      respond(200, {
        ...initial(),
        settings: [{ ...AUTO, value: 36, revision: 3, source: "override" }, LATE, W],
      }),
    );
    await u.click(screen.getByRole("button", { name: "Reload settings" }));
    await waitFor(() =>
      expect(screen.getByTestId(`current-${AUTO.key}`)).toHaveTextContent("36 hours"),
    );
  });

  it("shows the core's validation message for a 422 and a calm message for an outage", async () => {
    const u = userEvent.setup();
    fetchMock.mockResolvedValueOnce(
      respond(422, { error: "constraint_violated", message: "Not allowed: grace must be shorter" }),
    );
    render(<SettingsEditor initial={initial()} />);
    await u.click(screen.getByRole("button", { name: `Edit ${AUTO.key}` }));
    const input = screen.getByLabelText(/New value/);
    await u.clear(input);
    await u.type(input, "24");
    await u.type(screen.getByLabelText(/reason for change/i), REASON);
    await u.click(screen.getByRole("button", { name: "Save change" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/grace must be shorter/),
    );
    fetchMock.mockResolvedValueOnce(respond(503, { error: "unavailable" }));
    await u.click(screen.getByRole("button", { name: "Save change" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/not reachable/));
  });

  it("rollback needs a reason and sends only op, key, reason and revision", async () => {
    const u = userEvent.setup();
    const over = { ...AUTO, value: 24, revision: 2, source: "override" as const };
    fetchMock.mockResolvedValueOnce(
      respond(200, { ...AUTO, value: 36, revision: 3, source: "override" }),
    );
    render(<SettingsEditor initial={{ ...initial(), settings: [over, LATE, W] }} />);
    await u.click(screen.getByRole("button", { name: `Roll back ${AUTO.key}` }));
    await u.click(screen.getByRole("button", { name: /Roll back to the previous value/ }));
    expect(screen.getByText(/reason of at least 10 characters/)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    await u.type(screen.getByLabelText(/reason for change/i), REASON);
    await u.click(screen.getByRole("button", { name: /Roll back to the previous value/ }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(posted()).toEqual({ op: "rollback", key: AUTO.key, reason: REASON, revision: 2 });
  });

  it("a scheduled change can be cancelled, and edit is not offered while one is waiting", async () => {
    const u = userEvent.setup();
    const pend = {
      ...LATE,
      revision: 1,
      pending: { revision: 1, value: 12, effective_at: "2099-03-01T21:00:00.000Z" },
    };
    fetchMock.mockResolvedValueOnce(respond(200, { ...LATE, revision: 2 }));
    render(<SettingsEditor initial={{ ...initial(), settings: [AUTO, pend, W] }} />);
    expect(screen.queryByRole("button", { name: `Edit ${LATE.key}` })).toBeNull();
    await u.click(screen.getByRole("button", { name: `Cancel scheduled change to ${LATE.key}` }));
    await u.type(screen.getByLabelText(/reason for change/i), REASON);
    await u.click(screen.getByRole("button", { name: "Cancel the scheduled change" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(posted()).toEqual({ op: "cancel", key: LATE.key, reason: REASON, revision: 1 });
  });

  it("shows the history of a setting with who, why and the before and after", async () => {
    const u = userEvent.setup();
    fetchMock.mockResolvedValueOnce(
      respond(200, {
        key: AUTO.key,
        next_before: null,
        changes: [
          {
            revision: 2,
            op: "set",
            value: 24,
            previous_value: 36,
            effective_at: "2027-03-01T08:00:00.000Z",
            cancels_revision: null,
            reason: "second change",
            actor_id: "u_super",
            created_at: "x",
          },
          {
            revision: 1,
            op: "set",
            value: 36,
            previous_value: 48,
            effective_at: "2027-03-01T07:00:00.000Z",
            cancels_revision: null,
            reason: "first change",
            actor_id: "u_super",
            created_at: "x",
          },
        ],
      }),
    );
    render(<SettingsEditor initial={initial()} />);
    await u.click(screen.getByRole("button", { name: `History of ${AUTO.key}` }));
    const table = await screen.findByRole("table", { name: "History" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(table).toHaveTextContent("36 hours → 24 hours");
    expect(table).toHaveTextContent("second change");
    expect(table).toHaveTextContent("u_super");
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe(`/api/settings?key=${AUTO.key}`);
  });
});
