import { apiClient } from "@/lib/api-client";
import type { LoginResponse, RefreshResponse, User, UserCreate, UserUpdate } from "@/types/api";

/**
 * Auth endpoints — maps 1:1 to the `auth` tag in the OpenAPI spec.
 * Provenance: POST /api/v2/register, /login, /refresh, GET/PUT /api/v2/me.
 */
export const authApi = {
  register: (payload: UserCreate) => apiClient.post<User>("/api/v2/register", payload).then((r) => r.data),

  login: (email: string, password: string) => {
    const form = new URLSearchParams();
    form.set("grant_type", "password");
    form.set("username", email);
    form.set("password", password);
    return apiClient
      .post<LoginResponse>("/api/v2/login", form, {
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
      })
      .then((r) => r.data);
  },

  refresh: (refreshToken: string) =>
    apiClient
      .post<RefreshResponse>("/api/v2/refresh", null, { params: { refresh_token: refreshToken } })
      .then((r) => r.data),

  me: () => apiClient.get<User>("/api/v2/me").then((r) => r.data),

  updateMe: (payload: UserUpdate) => apiClient.put<User>("/api/v2/me", payload).then((r) => r.data),
};
