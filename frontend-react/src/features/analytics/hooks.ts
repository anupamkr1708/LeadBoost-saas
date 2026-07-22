"use client";

import { useQuery } from "@tanstack/react-query";
import { analyticsApi } from "@/features/analytics/api";

export function usePipelineMetrics(hours?: number) {
  return useQuery({
    queryKey: ["analytics", "pipeline", hours],
    queryFn: () => analyticsApi.pipelineMetrics(hours),
    staleTime: 30_000,
  });
}

export function useEvaluationMetrics(hours?: number) {
  return useQuery({
    queryKey: ["analytics", "evaluation", hours],
    queryFn: () => analyticsApi.evaluationMetrics(hours),
    staleTime: 30_000,
  });
}

export function useDiscoveryMetrics(hours?: number) {
  return useQuery({
    queryKey: ["analytics", "discovery", hours],
    queryFn: () => analyticsApi.discoveryMetrics(hours),
    staleTime: 30_000,
  });
}
