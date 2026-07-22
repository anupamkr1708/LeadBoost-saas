"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { leadsApi } from "@/features/leads/api";
import { normalizeApiError } from "@/lib/api-client";
import type { LeadCreate, LeadProcessRequest, LeadUpdate } from "@/types/api";

export function useLeads(params: { skip?: number; limit?: number } = {}) {
  return useQuery({
    queryKey: ["leads", params],
    queryFn: () => leadsApi.list(params),
    staleTime: 15_000,
  });
}

export function useLead(leadId: number | null) {
  return useQuery({
    queryKey: ["lead", leadId],
    queryFn: () => leadsApi.get(leadId as number),
    enabled: leadId !== null,
  });
}

export function useCreateLeadsFromUrls() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: LeadProcessRequest) => leadsApi.createFromUrls(payload),
    onSuccess: (leads) => {
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      toast.success(`${leads.length} lead${leads.length === 1 ? "" : "s"} queued.`);
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useCreateSingleLead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: LeadCreate) => leadsApi.createSingle(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      toast.success("Lead added.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useUpdateLead(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: LeadUpdate) => leadsApi.update(leadId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      queryClient.invalidateQueries({ queryKey: ["lead", leadId] });
      toast.success("Lead updated.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useDeleteLead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (leadId: number) => leadsApi.remove(leadId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      toast.success("Lead removed.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useProcessLead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (leadId: number) => leadsApi.process(leadId),
    onSuccess: (_data, leadId) => {
      queryClient.invalidateQueries({ queryKey: ["leads"] });
      queryClient.invalidateQueries({ queryKey: ["lead", leadId] });
      toast.success("Processing started.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}
