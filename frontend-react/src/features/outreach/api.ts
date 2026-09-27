import { apiClient } from "@/lib/api-client";
import type {
  OutreachAction,
  OutreachActionCreatePayload,
  OutreachPolicy,
  OutreachPolicyUpdatePayload,
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

  create: (payload: OutreachActionCreatePayload) =>
    apiClient.post<OutreachAction>("/api/v2/outreach-actions", payload).then((r) => r.data),

  approve: (actionId: number) =>
    apiClient.post<OutreachAction>(`/api/v2/outreach-actions/${actionId}/approve`).then((r) => r.data),

  cancel: (actionId: number) =>
    apiClient.post<OutreachAction>(`/api/v2/outreach-actions/${actionId}/cancel`).then((r) => r.data),

  dispatch: (actionId: number) =>
    apiClient.post<OutreachAction>(`/api/v2/outreach-actions/${actionId}/dispatch`).then((r) => r.data),

  getPolicy: (orgId: number) =>
    apiClient.get<OutreachPolicy>(`/api/v2/organizations/${orgId}/outreach-policy`).then((r) => r.data),

  updatePolicy: (orgId: number, payload: OutreachPolicyUpdatePayload) =>
    apiClient.put<OutreachPolicy>(`/api/v2/organizations/${orgId}/outreach-policy`, payload).then((r) => r.data),
};
