"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { outreachApi } from "@/features/outreach/api";
import { normalizeApiError } from "@/lib/api-client";
import type {
  OutreachActionCreatePayload,
  OutreachPolicyUpdatePayload,
  OutreachReplyCreatePayload,
  ReplyDraftState,
} from "@/types/api";

const listKey = (leadId?: number) => ["outreach-actions", leadId ?? "all"];
const policyKey = (orgId: number) => ["outreach-policy", orgId];
const mailerStateKey = (actionId: number) => ["outreach-mailer-state", actionId];
const replyDraftKey = (actionId: number) => ["outreach-reply-draft", actionId];

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

/** C9.3 -- the Mailing Agent's delivery state and conversation for one action, via the
 * LeadBoost API. Read-only: no mutation, no store, no polling -- it refetches only on
 * mount / window focus / an explicit refetch(), like any other query here. `enabled`
 * lets the caller skip the request entirely (e.g. before a first dispatch attempt). */
export function useOutreachMailerState(actionId: number, enabled = true) {
  return useQuery({
    queryKey: mailerStateKey(actionId),
    queryFn: () => outreachApi.getMailerState(actionId),
    enabled: enabled && actionId > 0,
    staleTime: 15_000,
    retry: 1,
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

// ===========================================================================================================
// C9.5 -- managed replies: create -> generate draft -> review the exact text -> approve it -> send it.
// Every call is to the LeadBoost API. Nothing here can send mail by itself: generation is held server-side,
// approval records a decision, and only the separate, explicit "send" mutation releases the approved draft.
// ===========================================================================================================

// Polling policy. Poll ONLY while the draft is still moving (being generated, or released and not yet sent) and
// stop for every reviewable or final state; back off; and give up after a bounded number of reads so a stuck
// draft can never poll forever. After giving up the user still has an explicit "Check again".
const POLL_BASE_MS = 2_000;
const POLL_MAX_MS = 15_000;
export const REPLY_POLL_MAX_ATTEMPTS = 30;
const POLLING_STATES: ReadonlySet<ReplyDraftState> = new Set<ReplyDraftState>(["generating", "queued", "sending"]);

/** The interval until the next read, or `false` to stop. Pure, so the policy is testable on its own. */
export function replyPollInterval(state: ReplyDraftState | undefined, updateCount: number): number | false {
  if (!state || !POLLING_STATES.has(state)) return false;
  if (updateCount >= REPLY_POLL_MAX_ATTEMPTS) return false;
  return Math.min(POLL_BASE_MS * 1.5 ** Math.max(0, updateCount), POLL_MAX_MS);
}

/** Bounded retry of a failed read: transient failures (network, 5xx) only, at most twice. A 4xx is an answer,
 * not a glitch -- retrying it would only delay the user's error. */
export function shouldRetryReplyRead(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  const status = normalizeApiError(error).status;
  return status === null || status >= 500;
}

/** The Mailing Agent's persisted draft for one reply action (read-only; never mutates anything). `enabled`
 * should be false until generation has been requested -- reading earlier would only report "not started". */
export function useReplyDraft(actionId: number, enabled = true) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: replyDraftKey(actionId),
    queryFn: () => outreachApi.getReplyDraft(actionId),
    enabled: enabled && actionId > 0,
    staleTime: 0,
    retry: shouldRetryReplyRead,
    retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
    refetchIntervalInBackground: false,
    refetchInterval: (q) =>
      q.state.status === "error" ? false : replyPollInterval(q.state.data?.state, q.state.dataUpdateCount),
  });
  // True once automatic polling has given up while the draft is still moving -- the UI then offers a manual
  // "Check again" instead of silently waiting forever.
  const reads = queryClient.getQueryState(replyDraftKey(actionId))?.dataUpdateCount ?? 0;
  const pollsExhausted =
    !!query.data && POLLING_STATES.has(query.data.state) && reads >= REPLY_POLL_MAX_ATTEMPTS;
  return { ...query, pollsExhausted };
}

/** Create the one reply action for an inbound message (sends and generates nothing). */
export function useCreateReplyAction(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: OutreachReplyCreatePayload) => outreachApi.createReply(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: listKey(leadId) }),
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

/** Ask for the draft to be GENERATED. Not a send. Safe to repeat (the server replays the same operation). */
export function useStartReplyDraft(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actionId: number) => outreachApi.startReplyDraft(actionId),
    onSuccess: (draft, actionId) => {
      queryClient.setQueryData(replyDraftKey(actionId), draft);
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
    },
    onError: (error) => {
      // The request may have been ACCEPTED even though the immediate read-back failed (the server records it first),
      // so re-read the action: the composer then shows the true state and recovers through the draft read.
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      toast.error(normalizeApiError(error).message);
    },
  });
}

/** Approve the EXACT draft that was displayed, identified by its digest. This records a decision only: it does
 * not send anything. A draft that changed since it was shown is refused by the server; the draft is re-read so
 * the user can review the current text. */
export function useApproveReplyDraft(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ actionId, contentSha256 }: { actionId: number; contentSha256: string }) =>
      outreachApi.approveReply(actionId, contentSha256),
    onSuccess: (_action, { actionId }) => {
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      queryClient.invalidateQueries({ queryKey: replyDraftKey(actionId) });
      toast.success("Draft approved. Nothing has been sent yet.");
    },
    onError: (error, { actionId }) => {
      const { message, errorCode } = normalizeApiError(error);
      queryClient.invalidateQueries({ queryKey: replyDraftKey(actionId) });
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      if (errorCode === "reply_draft_stale") {
        toast.error("This draft changed after you reviewed it. Please review the current version.");
      } else {
        toast.error(message);
      }
    },
  });
}

/** The explicit "Send approved reply": releases the draft that was approved. `conversationActionId` is the
 * action whose conversation view should be refreshed once the send is under way. */
export function useSendApprovedReply(leadId: number, conversationActionId: number) {
  const queryClient = useQueryClient();
  const refresh = (actionId: number) => {
    queryClient.invalidateQueries({ queryKey: listKey(leadId) });
    queryClient.invalidateQueries({ queryKey: replyDraftKey(actionId) });
    queryClient.invalidateQueries({ queryKey: mailerStateKey(conversationActionId) });
  };
  return useMutation({
    mutationFn: (actionId: number) => outreachApi.dispatch(actionId),
    onSuccess: (action, actionId) => {
      refresh(actionId);
      if (action.state === "submitted") {
        toast.success("Reply released for sending.");
      } else {
        // last_dispatch_error is a closed code, but still not something to show verbatim.
        toast.error("Could not release this reply yet. You can retry.");
      }
    },
    onError: (error, actionId) => {
      refresh(actionId);
      const { message, errorCode } = normalizeApiError(error);
      if (errorCode === "invalid_state_transition") {
        toast("This reply is already being sent.", { icon: "⏳" });
      } else {
        toast.error(message);
      }
    },
  });
}

/** Cancel a reply and revoke its held draft. */
export function useCancelReplyAction(leadId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (actionId: number) => outreachApi.cancel(actionId),
    onSuccess: (_a, actionId) => {
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      queryClient.invalidateQueries({ queryKey: replyDraftKey(actionId) });
      toast.success("Reply cancelled.");
    },
    onError: (error, actionId) => {
      queryClient.invalidateQueries({ queryKey: listKey(leadId) });
      queryClient.invalidateQueries({ queryKey: replyDraftKey(actionId) });
      toast.error(normalizeApiError(error).message);
    },
  });
}

/** Re-read the Mailing Agent's conversation for one action (the actual outbound -> inbound -> outbound thread), so a
 * reply that has been sent shows up where the user reads the conversation. */
export function useRefreshMailerState(conversationActionId: number) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: mailerStateKey(conversationActionId) });
}
