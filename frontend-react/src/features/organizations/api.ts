import { apiClient } from "@/lib/api-client";
import type { Organization, OrganizationCreate, OrganizationUpdate, QualificationSettings, QualificationSettingsUpdate } from "@/types/api";

/**
 * Organization endpoints — maps 1:1 to the `organizations` tag in the OpenAPI spec.
 * Provenance: GET/POST /api/v2/organizations/, GET/PUT /api/v2/organizations/{org_id},
 * GET/PUT /api/v2/organizations/{org_id}/qualification-settings (P1.2).
 */
export const organizationsApi = {
  current: () => apiClient.get<Organization>("/api/v2/organizations/").then((r) => r.data),

  create: (payload: OrganizationCreate) =>
    apiClient.post<Organization>("/api/v2/organizations/", payload).then((r) => r.data),

  getById: (orgId: number) => apiClient.get<Organization>(`/api/v2/organizations/${orgId}`).then((r) => r.data),

  update: (orgId: number, payload: OrganizationUpdate) =>
    apiClient.put<Organization>(`/api/v2/organizations/${orgId}`, payload).then((r) => r.data),

  // P1.2: the organization's qualification_threshold — distinct from any
  // individual lead's score. See types/api.ts::QualificationSettings.
  getQualificationSettings: (orgId: number) =>
    apiClient.get<QualificationSettings>(`/api/v2/organizations/${orgId}/qualification-settings`).then((r) => r.data),

  updateQualificationSettings: (orgId: number, payload: QualificationSettingsUpdate) =>
    apiClient
      .put<QualificationSettings>(`/api/v2/organizations/${orgId}/qualification-settings`, payload)
      .then((r) => r.data),
};
