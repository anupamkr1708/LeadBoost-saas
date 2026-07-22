"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { billingApi } from "@/features/billing/api";
import { normalizeApiError } from "@/lib/api-client";

export function useUsage() {
  return useQuery({ queryKey: ["usage"], queryFn: () => billingApi.usage(), staleTime: 30_000 });
}

export function usePlans() {
  return useQuery({ queryKey: ["plans"], queryFn: () => billingApi.plans(), staleTime: 5 * 60_000 });
}

export function useUpgradePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (planName: string) => billingApi.upgrade(planName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["usage"] });
      queryClient.invalidateQueries({ queryKey: ["organization"] });
      toast.success("Plan upgraded.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useCancelSubscription() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (immediate: boolean) => billingApi.cancel(immediate),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["usage"] });
      queryClient.invalidateQueries({ queryKey: ["organization"] });
      toast.success("Subscription canceled.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}
