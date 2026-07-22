import { apiClient } from "@/lib/api-client";
import { DISCOVERY_TIMEOUT_MS } from "@/lib/constants";
import type { DiscoveryResponse, DiscoverySearchRequest } from "@/types/api";

/**
 * Discovery endpoint — the flagship natural-language business search.
 * Provenance: POST /api/v2/discovery/search.
 * Runs synchronously on the backend and can take minutes (resolve -> enrich ->
 * score -> create leads), so it gets its own long timeout instead of the
 * default fast-endpoint budget, plus an optional AbortSignal so the UI can
 * stop waiting on the response without pretending it stopped the backend job.
 */
export const discoveryApi = {
  search: (payload: DiscoverySearchRequest, options?: { signal?: AbortSignal }) =>
    apiClient
      .post<DiscoveryResponse>("/api/v2/discovery/search", payload, {
        timeout: DISCOVERY_TIMEOUT_MS,
        signal: options?.signal,
      })
      .then((r) => r.data),
};
