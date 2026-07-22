import { apiClient } from "@/lib/api-client";
import type { Organization, OrganizationCreate, OrganizationUpdate } from "@/types/api";

/**
 * Organization endpoints — maps 1:1 to the `organizations` tag in the OpenAPI spec.
 * Provenance: GET/POST /api/v2/organizations/, GET/PUT /api/v2/organizations/{org_id}.
 */
export const organizationsApi = {
  current: () => apiClient.get<Organization>("/api/v2/organizations/").then((r) => r.data),

  create: (payload: OrganizationCreate) =>
    apiClient.post<Organization>("/api/v2/organizations/", payload).then((r) => r.data),

  getById: (orgId: number) => apiClient.get<Organization>(`/api/v2/organizations/${orgId}`).then((r) => r.data),

  update: (orgId: number, payload: OrganizationUpdate) =>
    apiClient.put<Organization>(`/api/v2/organizations/${orgId}`, payload).then((r) => r.data),
};
