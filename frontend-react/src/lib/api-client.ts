import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";
import { API_BASE_URL, DEFAULT_TIMEOUT_MS } from "@/lib/constants";
import { useAuthStore } from "@/store/auth-store";
import type { ApiErrorShape, HTTPValidationError, RefreshResponse } from "@/types/api";

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: DEFAULT_TIMEOUT_MS,
});

// Attach the current access token to every outgoing request.
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

let refreshPromise: Promise<string | null> | null = null;

/**
 * Calls POST /api/v2/refresh?refresh_token=... exactly once even if several
 * requests 401 concurrently, sharing the in-flight promise between them.
 */
async function refreshAccessToken(): Promise<string | null> {
  const { refreshToken, setTokens, logout } = useAuthStore.getState();
  if (!refreshToken) return null;

  if (!refreshPromise) {
    refreshPromise = axios
      .post<RefreshResponse>(`${API_BASE_URL}/api/v2/refresh`, null, {
        params: { refresh_token: refreshToken },
      })
      .then((res) => {
        const newAccess = res.data.access_token;
        const newRefresh = res.data.refresh_token ?? refreshToken;
        setTokens(newAccess, newRefresh);
        return newAccess;
      })
      .catch(() => {
        logout();
        return null;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

// On a 401, attempt one silent refresh + retry; otherwise log the session out.
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as (InternalAxiosRequestConfig & { _retry?: boolean }) | undefined;

    if (error.response?.status === 401 && originalRequest && !originalRequest._retry) {
      originalRequest._retry = true;
      const newToken = await refreshAccessToken();
      if (newToken) {
        originalRequest.headers = originalRequest.headers ?? {};
        originalRequest.headers.set?.("Authorization", `Bearer ${newToken}`);
        return apiClient(originalRequest);
      }
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

/** Normalize any axios/HTTP error (validation errors, network failures) into one shape the UI can render. */
export function normalizeApiError(error: unknown): ApiErrorShape {
  if (axios.isCancel(error) || (axios.isAxiosError(error) && error.code === "ERR_CANCELED")) {
    return { status: null, message: "Search canceled." };
  }

  if (axios.isAxiosError(error)) {
    const status = error.response?.status ?? null;
    const data = error.response?.data as HTTPValidationError | { detail?: string } | undefined;

    if (data && Array.isArray((data as HTTPValidationError).detail)) {
      const detail = (data as HTTPValidationError).detail;
      const fieldErrors: Record<string, string> = {};
      detail.forEach((d) => {
        const field = d.loc[d.loc.length - 1];
        if (typeof field === "string") fieldErrors[field] = d.msg;
      });
      return {
        status,
        message: detail[0]?.msg ?? "Please check the form and try again.",
        fieldErrors,
      };
    }

    if (typeof (data as { detail?: string })?.detail === "string") {
      return { status, message: (data as { detail: string }).detail };
    }

    if (status === 401) return { status, message: "Your session has expired. Please sign in again." };
    if (status === 403) return { status, message: "You don't have permission to do that." };
    if (status === 404) return { status, message: "We couldn't find that." };
    if (status === 429) return { status, message: "You've hit a rate limit. Try again shortly." };
    if (status && status >= 500) return { status, message: "Something went wrong on our end. Please try again." };
    if (error.code === "ECONNABORTED") return { status: null, message: "The request timed out. Please try again." };
    if (!error.response) return { status: null, message: "Can't reach the server. Check your connection." };

    return { status, message: error.message || "Something went wrong." };
  }
  return { status: null, message: "Something unexpected happened." };
}
