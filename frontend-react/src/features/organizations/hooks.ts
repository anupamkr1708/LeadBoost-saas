"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { organizationsApi } from "@/features/organizations/api";
import { normalizeApiError } from "@/lib/api-client";
import type { OrganizationUpdate } from "@/types/api";

export function useOrganization() {
  return useQuery({
    queryKey: ["organization"],
    queryFn: () => organizationsApi.current(),
    staleTime: 30_000,
  });
}

export function useUpdateOrganization(orgId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: OrganizationUpdate) => organizationsApi.update(orgId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["organization"] });
      toast.success("Organization updated.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}
