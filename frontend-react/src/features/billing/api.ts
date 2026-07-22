import { apiClient } from "@/lib/api-client";
import type { PlanOption, PlanUsage } from "@/types/api";

/**
 * Billing endpoints — maps 1:1 to the `billing` tag in the OpenAPI spec.
 * Provenance: GET /api/v2/usage, GET /api/v2/plans, POST /api/v2/upgrade, POST /api/v2/cancel.
 * `/plans` has no fixed response schema server-side, so callers must treat entries defensively.
 */
export const billingApi = {
  usage: () => apiClient.get<PlanUsage>("/api/v2/usage").then((r) => r.data),

  plans: () => apiClient.get<PlanOption[] | Record<string, PlanOption>>("/api/v2/plans").then((r) => r.data),

  upgrade: (planName: string) =>
    apiClient.post("/api/v2/upgrade", null, { params: { plan_name: planName } }).then((r) => r.data),

  cancel: (immediate = false) =>
    apiClient.post("/api/v2/cancel", null, { params: { immediate } }).then((r) => r.data),
};
