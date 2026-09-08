import { apiClient } from "@/lib/api-client";
import type {
  EmailAccount,
  EmailAccountCreatePayload,
  EmailAccountUpdatePayload,
  EmailAccountVerifyResult,
} from "@/types/api";

/**
 * Email Account endpoints (P1.3) — maps 1:1 to the `email-accounts` tag
 * in the OpenAPI spec. Provenance: api/endpoints/email_accounts.py.
 *
 * SECURITY: none of these return types ever carry a credential — see
 * EmailAccount's docstring in types/api.ts. `credential` is accepted by
 * `create`/`update` only (write-only, plaintext on the wire, encrypted
 * server-side before persistence).
 */
export const emailAccountsApi = {
  list: (orgId: number) =>
    apiClient.get<EmailAccount[]>(`/api/v2/organizations/${orgId}/email-accounts`).then((r) => r.data),

  create: (orgId: number, payload: EmailAccountCreatePayload) =>
    apiClient.post<EmailAccount>(`/api/v2/organizations/${orgId}/email-accounts`, payload).then((r) => r.data),

  update: (orgId: number, accountId: number, payload: EmailAccountUpdatePayload) =>
    apiClient
      .patch<EmailAccount>(`/api/v2/organizations/${orgId}/email-accounts/${accountId}`, payload)
      .then((r) => r.data),

  remove: (orgId: number, accountId: number) =>
    apiClient.delete<EmailAccount>(`/api/v2/organizations/${orgId}/email-accounts/${accountId}`).then((r) => r.data),

  verify: (orgId: number, accountId: number) =>
    apiClient
      .post<EmailAccountVerifyResult>(`/api/v2/organizations/${orgId}/email-accounts/${accountId}/verify`)
      .then((r) => r.data),
};
