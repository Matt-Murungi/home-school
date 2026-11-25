import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

import { SearchBox } from "@/components/SearchBox";

type G = {
  type: string;
  label: string;
  hits: { id: string; type: string; title: string; subtitle: string; href: string | null }[];
  hasMore: boolean;
  seeAll: string | null;
};
const group = (over: Partial<G> = {}): G => ({
  type: "parent",
  label: "Parents",
  hasMore: false,
  seeAll: null,
  hits: [
    {
      id: "p1",
      type: "parent",
      title: "Grace Namuli",
      subtitle: "+256•••••456",
      href: "/families",
    },
  ],
  ...over,
});
const json = (body: unknown, init: ResponseInit & { redirected?: boolean } = {}) => {
  const res = new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (init.redirected) Object.defineProperty(res, "redirected", { value: true });
  return res;
};
let fetchMock: ReturnType<typeof vi.fn>;
const bodyOf = (call: unknown[]) => JSON.parse((call[1] as RequestInit).body as string);

function setup(groups = ["Parents", "Payments"]) {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  render(<SearchBox groups={groups} />);
  return { user, input: screen.getByRole("combobox", { name: /search/i }) };
}
const settle = async (ms = 300) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  push.mockReset();
  fetchMock = vi.fn(async () => json({ groups: [group()] }));
  vi.stubGlobal("fetch", fetchMock);
  Object.defineProperty(window.navigator, "onLine", { value: true, configurable: true });
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("combobox semantics and shortcuts", () => {
  it("exposes the ARIA combobox pattern", () => {
    const { input } = setup();
    expect(input).toHaveAttribute("aria-autocomplete", "list");
    expect(input).toHaveAttribute("aria-expanded", "false");
    expect(input).toHaveAttribute("aria-controls");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(input).toHaveAttribute("maxlength", "64");
    expect(input).toHaveAttribute("aria-keyshortcuts", expect.stringContaining("/"));
    expect(input.getAttribute("placeholder")).toMatch(/Parents, Payments/);
  });
  it("'/' and Ctrl/Cmd+K focus the box; '/' is ignored while typing elsewhere", async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <>
        <SearchBox groups={["Parents"]} />
        <textarea aria-label="notes" />
      </>,
    );
    const input = screen.getByRole("combobox");
    await user.keyboard("/");
    expect(input).toHaveFocus();
    await user.tab({ shift: true });
    await user.click(screen.getByLabelText("notes"));
    await user.keyboard("/");
    expect(screen.getByLabelText("notes")).toHaveValue("/");
    await user.keyboard("{Control>}k{/Control}");
    expect(input).toHaveFocus();
    screen.getByLabelText("notes").focus();
    await user.keyboard("{Meta>}k{/Meta}");
    expect(input).toHaveFocus();
  });
  it("is disabled with an explanation when the role has nothing to search", () => {
    render(<SearchBox groups={[]} />);
    const input = screen.getByRole("combobox");
    expect(input).toBeDisabled();
    expect(screen.getByText(/not available for your role/i)).toBeInTheDocument();
  });
});

describe("debounce and requests", () => {
  it("waits for a pause, then sends one POST with the query in the body, never in the URL", async () => {
    const { user, input } = setup();
    await user.type(input, "grace");
    expect(fetchMock).not.toHaveBeenCalled();
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/search");
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).credentials).toBe("same-origin");
    expect(bodyOf(fetchMock.mock.calls[0])).toEqual({ q: "grace" });
    expect(window.location.href).not.toContain("grace");
  });
  it("does not search below the minimum length and says why", async () => {
    const { user, input } = setup();
    await user.type(input, "g");
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/at least 2 characters/i)).toBeInTheDocument();
    await user.clear(input);
    await user.type(input, "07");
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("trims before sending and does not search whitespace", async () => {
    const { user, input } = setup();
    await user.type(input, "   ");
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
    await user.type(input, "ab  ");
    await settle();
    expect(bodyOf(fetchMock.mock.calls[0]).q).toBe("ab");
  });
  it("an old slow response never overwrites a newer one, and the old request is aborted", async () => {
    let resolveSlow!: (r: Response) => void;
    const signals: AbortSignal[] = [];
    fetchMock.mockImplementationOnce((_u, init) => {
      signals.push((init as RequestInit).signal as AbortSignal);
      return new Promise<Response>((r) => (resolveSlow = r));
    });
    fetchMock.mockImplementationOnce(async (_u, init) => {
      signals.push((init as RequestInit).signal as AbortSignal);
      return json({
        groups: [
          group({
            hits: [
              { id: "p2", type: "parent", title: "NEW RESULT", subtitle: "", href: "/families" },
            ],
          }),
        ],
      });
    });
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    await user.type(input, "x");
    await settle();
    expect(await screen.findByText("NEW RESULT")).toBeInTheDocument();
    expect(signals[0].aborted).toBe(true);
    await act(async () => {
      resolveSlow(
        json({
          groups: [
            group({
              hits: [
                { id: "p1", type: "parent", title: "OLD RESULT", subtitle: "", href: "/families" },
              ],
            }),
          ],
        }),
      );
    });
    await settle(50);
    expect(screen.queryByText("OLD RESULT")).toBeNull();
    expect(screen.getByText("NEW RESULT")).toBeInTheDocument();
  });
  it("an old response arriving after the query was cleared is ignored", async () => {
    let resolveSlow!: (r: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>((r) => (resolveSlow = r)));
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    await user.clear(input);
    await act(async () => resolveSlow(json({ groups: [group()] })));
    expect(screen.queryByRole("option")).toBeNull();
    expect(input).toHaveAttribute("aria-expanded", "false");
  });
});

describe("results", () => {
  it("renders groups with headings and options; matched text is highlighted with <mark>", async () => {
    fetchMock.mockImplementation(async () =>
      json({
        groups: [
          group(),
          group({
            type: "payment",
            label: "Payments",
            hits: [
              {
                id: "pay1",
                type: "payment",
                title: "MP240101",
                subtitle: "UGX 45,000",
                href: "/payments",
              },
            ],
          }),
        ],
      }),
    );
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    const list = await screen.findByRole("listbox");
    expect(input).toHaveAttribute("aria-expanded", "true");
    expect(input).toHaveAttribute("aria-controls", list.id);
    const groups = within(list).getAllByRole("group");
    expect(groups.map((g) => g.getAttribute("aria-label") ?? g.textContent)).toEqual(
      expect.arrayContaining([expect.stringMatching(/Parents/), expect.stringMatching(/Payments/)]),
    );
    expect(within(list).getAllByRole("option")).toHaveLength(2);
    expect(list.querySelector("mark")?.textContent?.toLowerCase()).toBe("grace");
    expect(screen.getByRole("status")).toHaveTextContent(/2 results/i);
  });
  it("hostile titles are shown as text; no element is injected", async () => {
    const evil = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>`;
    fetchMock.mockImplementation(async () =>
      json({
        groups: [
          group({
            hits: [{ id: "p1", type: "parent", title: evil, subtitle: evil, href: "/families" }],
          }),
        ],
      }),
    );
    const { user, input } = setup();
    await user.type(input, "img");
    await settle();
    const list = await screen.findByRole("listbox");
    expect(list.querySelector("img, script")).toBeNull();
    expect(list.textContent).toContain(evil);
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });
  it("a hostile href from the server is not followed (only same-site paths navigate)", async () => {
    fetchMock.mockImplementation(async () =>
      json({
        groups: [
          group({
            hits: [
              {
                id: "p1",
                type: "parent",
                title: "Grace",
                subtitle: "",
                href: "javascript:alert(1)",
              },
            ],
          }),
        ],
      }),
    );
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    await screen.findByRole("listbox");
    await user.keyboard("{ArrowDown}{Enter}");
    expect(push).not.toHaveBeenCalled();
  });
  it("shows a 'see all' option only when the server provides one", async () => {
    fetchMock.mockImplementation(async () =>
      json({ groups: [group({ hasMore: true, seeAll: "/families" })] }),
    );
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    expect(await screen.findByRole("option", { name: /see all parents/i })).toBeInTheDocument();
  });
  it("RTL and unicode results render", async () => {
    fetchMock.mockImplementation(async () =>
      json({
        groups: [
          group({
            hits: [
              { id: "p1", type: "parent", title: "مريم علي", subtitle: "", href: "/families" },
            ],
          }),
        ],
      }),
    );
    const { user, input } = setup();
    await user.type(input, "مريم");
    await settle();
    expect(await screen.findByText(/علي/)).toBeInTheDocument();
    expect(bodyOf(fetchMock.mock.calls[0]).q).toBe("مريم");
  });
});

describe("keyboard-only operation", () => {
  async function open() {
    fetchMock.mockImplementation(async () =>
      json({
        groups: [
          group({
            hits: [
              { id: "p1", type: "parent", title: "Grace One", subtitle: "", href: "/families" },
              { id: "p2", type: "parent", title: "Grace Two", subtitle: "", href: "/families" },
            ],
          }),
          group({
            type: "teacher",
            label: "Teachers",
            hits: [
              {
                id: "t1",
                type: "teacher",
                title: "Grace Teacher",
                subtitle: "",
                href: "/teachers",
              },
            ],
          }),
        ],
      }),
    );
    const ctx = setup();
    await ctx.user.type(ctx.input, "grace");
    await settle();
    await screen.findByRole("listbox");
    return ctx;
  }
  it("arrow keys move aria-activedescendant through the flat list and wrap; focus stays in the input", async () => {
    const { user, input } = await open();
    const opts = screen.getAllByRole("option");
    await user.keyboard("{ArrowDown}");
    expect(input).toHaveAttribute("aria-activedescendant", opts[0].id);
    expect(opts[0]).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(input).toHaveAttribute("aria-activedescendant", opts[2].id);
    await user.keyboard("{ArrowDown}");
    expect(input).toHaveAttribute("aria-activedescendant", opts[0].id);
    await user.keyboard("{ArrowUp}");
    expect(input).toHaveAttribute("aria-activedescendant", opts[2].id);
    await user.keyboard("{Home}");
    expect(input).toHaveAttribute("aria-activedescendant", opts[0].id);
    await user.keyboard("{End}");
    expect(input).toHaveAttribute("aria-activedescendant", opts[2].id);
    expect(input).toHaveFocus();
  });
  it("Enter opens the active result; with none active it does nothing", async () => {
    const { user } = await open();
    await user.keyboard("{Enter}");
    expect(push).not.toHaveBeenCalled();
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{Enter}");
    expect(push).toHaveBeenCalledWith("/teachers");
  });
  it("Escape closes the list, a second Escape clears the box, focus stays", async () => {
    const { user, input } = await open();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(input).toHaveValue("grace");
    expect(input).toHaveAttribute("aria-expanded", "false");
    await user.keyboard("{Escape}");
    expect(input).toHaveValue("");
    expect(input).toHaveFocus();
  });
  it("typing again resets the active option; clicking a result navigates; outside click closes", async () => {
    const { user, input } = await open();
    await user.keyboard("{ArrowDown}");
    await user.type(input, "z");
    expect(input).not.toHaveAttribute("aria-activedescendant");
    await settle();
    await user.click((await screen.findAllByRole("option"))[1]);
    expect(push).toHaveBeenCalledWith("/families");
    expect(screen.queryByRole("listbox")).toBeNull();
  });
  it("Tab leaves the box and closes the list", async () => {
    const { user } = await open();
    await user.tab();
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});

describe("empty, loading, error and offline states", () => {
  it("empty", async () => {
    fetchMock.mockImplementation(async () => json({ groups: [] }));
    const { user, input } = setup();
    await user.type(input, "nobody");
    await settle();
    expect(await screen.findByText(/no results/i)).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-expanded", "true");
  });
  it("loading is announced while a request is in flight", async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    expect(screen.getByRole("status")).toHaveTextContent(/searching/i);
  });
  it("server error offers retry, and retry searches again", async () => {
    fetchMock.mockResolvedValueOnce(json({ error: "unavailable" }, { status: 503 }));
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t search/i);
    await user.click(screen.getByRole("button", { name: /retry/i }));
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(await screen.findByRole("option", { name: /Grace Namuli/ })).toBeInTheDocument();
  });
  it("a network failure is an error, not a crash", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
  it("429 tells the admin to wait", async () => {
    fetchMock.mockResolvedValueOnce(
      json({ error: "rate_limited" }, { status: 429, headers: { "retry-after": "7" } }),
    );
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    expect(await screen.findByRole("alert")).toHaveTextContent(/too many searches.*7/i);
  });
  it("an expired session (redirected response or HTML) says so instead of rendering the page", async () => {
    fetchMock.mockResolvedValueOnce(json({}, { redirected: true }));
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    expect(await screen.findByRole("alert")).toHaveTextContent(/sign in again|session/i);
    fetchMock.mockResolvedValueOnce(
      new Response("<html>login</html>", { status: 200, headers: { "content-type": "text/html" } }),
    );
    await user.type(input, "x");
    await settle();
    expect(await screen.findByRole("alert")).toHaveTextContent(/sign in again|session/i);
  });
  it("offline: no request is made and the state is explained; it recovers when back online", async () => {
    Object.defineProperty(window.navigator, "onLine", { value: false, configurable: true });
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/offline/i);
    Object.defineProperty(window.navigator, "onLine", { value: true, configurable: true });
    await act(async () => {
      window.dispatchEvent(new Event("online"));
    });
    await settle();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  });
  it("groups the server could not search are named", async () => {
    fetchMock.mockImplementation(async () => json({ groups: [group()], unavailable: ["payment"] }));
    const { user, input } = setup();
    await user.type(input, "grace");
    await settle();
    expect(await screen.findByText(/payments could not be searched/i)).toBeInTheDocument();
  });
});
