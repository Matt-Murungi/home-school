import type { EntityType } from "./policy";
import type { RawHit, SearchSource, SourceRequest } from "./types";

type Opts = {
  /** Simulates a broken source that ignores the requested type and scope. */
  leaky?: boolean;
  failTypes?: readonly EntityType[];
};

/** In-memory stand-in for the Appwrite source: same contract, substring matching. */
export class FakeSearchSource implements SearchSource {
  calls: SourceRequest[] = [];
  constructor(
    private records: RawHit[],
    private opts: Opts = {},
  ) {}

  async search(req: SourceRequest): Promise<RawHit[]> {
    this.calls.push(structuredClone(req));
    if (this.opts.failTypes?.includes(req.type)) throw new Error("boom: fake source failure");
    const { text, phoneFragment, dateRange } = req.query;
    const needle = text.toLowerCase();
    const matches = (r: RawHit) => {
      const values = [r.id, ...Object.values(r.fields)].map((v) => String(v ?? "").toLowerCase());
      if (values.some((v) => v.includes(needle))) return true;
      const phone = String(r.fields.phone ?? "").replace(/\D/g, "");
      if (phoneFragment && phone.includes(phoneFragment)) return true;
      const at = Date.parse(String(r.fields.scheduled_start_at ?? ""));
      return !!dateRange && at >= Date.parse(dateRange.from) && at < Date.parse(dateRange.to);
    };
    return this.records
      .filter(
        (r) => this.opts.leaky || (r.type === req.type && (req.scope === "all" || r.linkedToCase)),
      )
      .filter(matches)
      .slice(0, req.limit)
      .map((r) => structuredClone(r));
  }
}
