"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { outreachApi } from "@/features/outreach/api";
import { normalizeApiError } from "@/lib/api-client";
import type { OutreachActionCreatePayload, OutreachPolicyUpdatePayload } from "@/types/api";

const listKey = (leadId?: number) => ["outreach-actions", leadId ?? "all"];
const policyKey = (orgId: number) => ["outreach-policy", orgId];

/** Outreach actions for a single lead -- the lead detail view's own
 * outreach history (mirrors how ai_insights is fetched per-lead). */
export function useOutreachActionsForLead(leadId: number) {
  return useQuery({
    queryKey: listKey(leadId),
    queryFn: () => outreachApi.list({ leadId }),
    enabled: leadId > 0,
    staleTime: 10_000,
  });
}

export function useCreateOutreachAction(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: OutreachActionCreatePayload) => outreachApi.create(payload),
    onSuccess: (action) => {
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      if (action.state === "approved") {
        toast.success("Outreach authorized and approved automatically.");
      } else {
        toast.success("Outreach prepared — awaiting approval.");
      }
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useApproveOutreachAction(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actionId: number) => outreachApi.approve(actionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      toast.success("Outreach approved.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useCancelOutreachAction(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actionId: number) => outreachApi.cancel(actionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      toast.success("Outreach cancelled.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useDispatchOutreachAction(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actionId: number) => outreachApi.dispatch(actionId),
    onSuccess: (action) => {
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      if (action.state === "submitted") {
        toast.success("Handed off to the Mailing Agent.");
      } else {
        // Safe, generic copy -- last_dispatch_error is a closed-vocabulary
        // code (see DispatchErrorCode), not raw provider/exception text,
        // but still not something to show verbatim in a toast.
        toast.error("Could not hand this off to the Mailing Agent yet. You can retry.");
      }
    },
    onError: (error) => {
      // A losing concurrent dispatch request (see backend
      // OutreachState.DISPATCHING) surfaces as invalid_state_transition
      // -- a quieter, more accurate message than the generic error text
      // for that one case.
      const { message, errorCode } = normalizeApiError(error);
      if (errorCode === "invalid_state_transition") {
        toast("This outreach action is already being sent.", { icon: "⏳" });
      } else {
        toast.error(message);
      }
    },
  });
}

export function useOutreachPolicy(orgId: number) {
  return useQuery({
    queryKey: policyKey(orgId),
    queryFn: () => outreachApi.getPolicy(orgId),
    enabled: orgId > 0,
    staleTime: 30_000,
  });
}

export function useUpdateOutreachPolicy(orgId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: OutreachPolicyUpdatePayload) => outreachApi.updatePolicy(orgId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: policyKey(orgId) });
      toast.success("Outreach policy updated.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}
