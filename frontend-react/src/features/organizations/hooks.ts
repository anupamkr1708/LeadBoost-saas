"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { organizationsApi } from "@/features/organizations/api";
import { normalizeApiError } from "@/lib/api-client";
import type { OrganizationUpdate, QualificationSettingsUpdate } from "@/types/api";

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

// P1.2: organization qualification threshold — see types/api.ts::QualificationSettings.
export function useQualificationSettings(orgId: number) {
  return useQuery({
    queryKey: ["qualification-settings", orgId],
    queryFn: () => organizationsApi.getQualificationSettings(orgId),
    enabled: orgId > 0,
    staleTime: 30_000,
  });
}

export function useUpdateQualificationSettings(orgId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: QualificationSettingsUpdate) => organizationsApi.updateQualificationSettings(orgId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["qualification-settings", orgId] });
      // Every already-fetched lead's `is_qualified` was derived from the
      // old threshold — refetch so the leads list/dashboard reflect the
      // new one immediately, without needing a page reload.
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      toast.success("Qualification settings updated.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}
