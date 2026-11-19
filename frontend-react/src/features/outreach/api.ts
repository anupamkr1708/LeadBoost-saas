import { apiClient } from "@/lib/api-client";
import type {
  OutreachAction,
  OutreachActionCreatePayload,
  OutreachMailerState,
  OutreachPolicy,
  OutreachPolicyUpdatePayload,
  OutreachReplyCreatePayload,
  ReplyDraft,
} from "@/types/api";

/**
 * Outreach endpoints (P1.4). Provenance: api/endpoints/outreach.py.
 *
 * Unlike email-accounts, these are NOT nested under
 * /organizations/{orgId} (except the policy sub-resource) -- they follow
 * the same flat, current-user-derived-organization convention as
 * /leads. See that file's own module docstring for why.
 */
export const outreachApi = {
  list: (params?: { leadId?: number; state?: string }) =>
    apiClient
      .get<OutreachAction[]>("/api/v2/outreach-actions", {
        params: { lead_id: params?.leadId, state: params?.state },
      })
      .then((r) => r.data),

  get: (actionId: number) =>
    apiClient.get<OutreachAction>(`/api/v2/outreach-actions/${actionId}`).then((r) => r.data),

  /** C9.3 -- read-only. The Mailing Agent is reached by the LeadBoost backend, never by the browser. */
  getMailerState: (actionId: number) =>
    apiClient.get<OutreachMailerState>(`/api/v2/outreach-actions/${actionId}/mailer-state`).then((r) => r.data),

  create: (payload: OutreachActionCreatePayload) =>
    apiClient.post<OutreachAction>("/api/v2/outreach-actions", payload).then((r) => r.data),

  approve: (actionId: number) =>
    apiClient.post<OutreachAction>(`/api/v2/outreach-actions/${actionId}/approve`).then((r) => r.data),

  cancel: (actionId: number) =>
    apiClient.post<OutreachAction>(`/api/v2/outreach-actions/${actionId}/cancel`).then((r) => r.data),

  dispatch: (actionId: number) =>
    apiClient.post<OutreachAction>(`/api/v2/outreach-actions/${actionId}/dispatch`).then((r) => r.data),

  // ---- C9.5 managed replies. Every call goes to the LeadBoost API; the browser never talks to the Mailing Agent.
  /** Create the (one) reply action for an inbound message. Sends nothing and generates nothing. */
  createReply: (payload: OutreachReplyCreatePayload) =>
    apiClient.post<OutreachAction>("/api/v2/outreach-actions", payload).then((r) => r.data),

  /** Ask the Mailing Agent to GENERATE the draft. Not a send: the draft is held until approved and sent. Idempotent. */
  startReplyDraft: (actionId: number) =>
    apiClient.post<ReplyDraft>(`/api/v2/outreach-actions/${actionId}/reply-draft`).then((r) => r.data),

  /** Read-only: the exact persisted subject/body. */
  getReplyDraft: (actionId: number) =>
    apiClient.get<ReplyDraft>(`/api/v2/outreach-actions/${actionId}/reply-draft`).then((r) => r.data),

  /** Approve the EXACT draft that was on screen, identified by its digest. The server re-checks it. */
  approveReply: (actionId: number, contentSha256: string) =>
    apiClient
      .post<OutreachAction>(`/api/v2/outreach-actions/${actionId}/approve`, { content_sha256: contentSha256 })
      .then((r) => r.data),

  getPolicy: (orgId: number) =>
    apiClient.get<OutreachPolicy>(`/api/v2/organizations/${orgId}/outreach-policy`).then((r) => r.data),

  updatePolicy: (orgId: number, payload: OutreachPolicyUpdatePayload) =>
    apiClient.put<OutreachPolicy>(`/api/v2/organizations/${orgId}/outreach-policy`, payload).then((r) => r.data),
};
