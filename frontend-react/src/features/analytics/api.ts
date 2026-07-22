import { apiClient } from "@/lib/api-client";
import type { DiscoveryMetricsSummary, EvaluationMetricsSummary, PipelineMetricsSummary } from "@/types/api";

/**
 * Analytics endpoints — maps 1:1 to the `analytics` tag in the OpenAPI spec.
 * Provenance: GET /api/v2/analytics/{pipeline-metrics,evaluation-metrics,discovery-metrics}.
 * All accept an optional `hours` window; omitting it returns all-time aggregates.
 */
export const analyticsApi = {
  pipelineMetrics: (hours?: number) =>
    apiClient
      .get<PipelineMetricsSummary>("/api/v2/analytics/pipeline-metrics", { params: { hours } })
      .then((r) => r.data),

  evaluationMetrics: (hours?: number) =>
    apiClient
      .get<EvaluationMetricsSummary>("/api/v2/analytics/evaluation-metrics", { params: { hours } })
      .then((r) => r.data),

  discoveryMetrics: (hours?: number) =>
    apiClient
      .get<DiscoveryMetricsSummary>("/api/v2/analytics/discovery-metrics", { params: { hours } })
      .then((r) => r.data),
};
