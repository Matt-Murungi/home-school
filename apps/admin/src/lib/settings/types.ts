/** Shapes of the core service's settings API (`/v1/admin/settings`, H05 #134). */
export type SettingValue = number | Record<string, number>;
export type FieldDef = { name: string; min: number; max: number };

export type Pending = { revision: number; value: SettingValue; effective_at: string };

export type SettingView = {
  key: string;
  group: string;
  kind: "int" | "float" | "map";
  description: string;
  unit: string;
  applies: "immediate" | "next_day";
  requires_reason: boolean;
  restart_required: boolean;
  min?: number;
  max?: number;
  fields?: FieldDef[];
  sum_to?: number;
  value: SettingValue;
  default: SettingValue;
  source: "default" | "override";
  revision: number;
  pending: Pending | null;
};

export type SettingsList = { groups: { id: string; title: string }[]; settings: SettingView[] };

export type HistoryEntry = {
  revision: number;
  op: "set" | "rollback" | "cancel";
  value: SettingValue | null;
  previous_value: SettingValue | null;
  effective_at: string | null;
  cancels_revision: number | null;
  reason: string | null;
  actor_id: string | null;
  created_at: string | null;
};
export type HistoryPage = { key: string; changes: HistoryEntry[]; next_before: number | null };

/** What the browser may ask the admin route to do (the route validates this shape strictly). */
export type ChangeRequest =
  | {
      op: "set";
      key: string;
      value: SettingValue;
      reason: string;
      revision: number;
      effectiveAt?: string;
    }
  | { op: "rollback" | "cancel"; key: string; reason: string; revision: number };
