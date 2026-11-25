import type { SettingView } from "@/lib/settings/types";

const base = {
  group: "g",
  description: "d",
  applies: "immediate",
  requires_reason: true,
  restart_required: false,
  default: 1,
  source: "default",
  revision: 0,
  pending: null,
} as const;
export const INT: SettingView = {
  ...base,
  key: "session.auto_confirm_after_hours",
  kind: "int",
  unit: "hours",
  min: 1,
  max: 720,
  value: 48,
};
export const FLOAT: SettingView = {
  ...base,
  key: "travel.fallback_speed_kmh",
  kind: "float",
  unit: "km/h",
  min: 5,
  max: 120,
  value: 20,
};
export const WEIGHTS: SettingView = {
  ...base,
  key: "matching.weights",
  kind: "map",
  unit: "percent",
  sum_to: 100,
  fields: [
    "travel_time",
    "tier",
    "struggle_fit",
    "workload_balance",
    "reliability",
    "cluster_bonus",
  ].map((name) => ({ name, min: 0, max: 100 })),
  value: {
    travel_time: 30,
    tier: 20,
    struggle_fit: 15,
    workload_balance: 15,
    reliability: 15,
    cluster_bonus: 5,
  },
  default: {
    travel_time: 30,
    tier: 20,
    struggle_fit: 15,
    workload_balance: 15,
    reliability: 15,
    cluster_bonus: 5,
  },
};
