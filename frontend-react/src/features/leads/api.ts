import { apiClient } from "@/lib/api-client";
import type { Lead, LeadCreate, LeadDetail, LeadProcessRequest, LeadUpdate } from "@/types/api";

/**
 * Leads endpoints — maps 1:1 to the `leads` tag in the OpenAPI spec.
 * Provenance: POST/GET /api/v2/leads/, POST /api/v2/leads/single,
 * GET/PUT/DELETE /api/v2/leads/{lead_id}, POST /api/v2/leads/{lead_id}/process.
 */
export const leadsApi = {
  list: (params: { skip?: number; limit?: number } = {}) =>
    apiClient.get<Lead[]>("/api/v2/leads/", { params }).then((r) => r.data),

  createFromUrls: (payload: LeadProcessRequest) =>
    apiClient.post<Lead[]>("/api/v2/leads/", payload).then((r) => r.data),

  createSingle: (payload: LeadCreate) => apiClient.post<Lead>("/api/v2/leads/single", payload).then((r) => r.data),

  // GET /leads/{id} is the one endpoint that returns the richer LeadDetail
  // shape (adds `ai_insights`: Company Intelligence, Decision, Evaluation,
  // Review, and Messaging output) — every other lead endpoint returns the
  // plain `Lead` shape.
  get: (leadId: number) => apiClient.get<LeadDetail>(`/api/v2/leads/${leadId}`).then((r) => r.data),

  update: (leadId: number, payload: LeadUpdate) =>
    apiClient.put<Lead>(`/api/v2/leads/${leadId}`, payload).then((r) => r.data),

  remove: (leadId: number) => apiClient.delete(`/api/v2/leads/${leadId}`).then((r) => r.data),

  process: (leadId: number) => apiClient.post(`/api/v2/leads/${leadId}/process`).then((r) => r.data),
};
