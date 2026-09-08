"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { emailAccountsApi } from "@/features/email-accounts/api";
import { normalizeApiError } from "@/lib/api-client";
import type { EmailAccountCreatePayload, EmailAccountUpdatePayload } from "@/types/api";

const key = (orgId: number) => ["email-accounts", orgId];

export function useEmailAccounts(orgId: number) {
  return useQuery({
    queryKey: key(orgId),
    queryFn: () => emailAccountsApi.list(orgId),
    enabled: orgId > 0,
    staleTime: 15_000,
  });
}

export function useCreateEmailAccount(orgId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: EmailAccountCreatePayload) => emailAccountsApi.create(orgId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: key(orgId) });
      toast.success("Email account added.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useUpdateEmailAccount(orgId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, payload }: { accountId: number; payload: EmailAccountUpdatePayload }) =>
      emailAccountsApi.update(orgId, accountId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: key(orgId) });
      toast.success("Email account updated.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useDeleteEmailAccount(orgId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (accountId: number) => emailAccountsApi.remove(orgId, accountId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: key(orgId) });
      toast.success("Email account disabled.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useVerifyEmailAccount(orgId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (accountId: number) => emailAccountsApi.verify(orgId, accountId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: key(orgId) });
      if (result.verification_status === "verified") {
        toast.success("Mailbox verified successfully.");
      } else if (result.verification_status === "disabled") {
        toast.error("This account is disabled — enable it before verifying.");
      } else {
        // Safe, generic copy only -- never surface raw SMTP/exception
        // text (there isn't any in this response to begin with; see
        // EmailAccountVerifyResult's error_code, a closed vocabulary).
        toast.error("Mailbox verification failed. Check the server settings or credentials.");
      }
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}
