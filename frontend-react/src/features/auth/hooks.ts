"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import toast from "react-hot-toast";
import { authApi } from "@/features/auth/api";
import { normalizeApiError } from "@/lib/api-client";
import { useAuthStore } from "@/store/auth-store";
import type { UserCreate, UserUpdate } from "@/types/api";

/** Fetches the current user; only runs once there's an access token in the store. */
export function useCurrentUser() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const setUser = useAuthStore((s) => s.setUser);
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const user = await authApi.me();
      setUser(user);
      return user;
    },
    enabled: !!accessToken,
    staleTime: 60_000,
    retry: false,
  });
}

export function useLogin() {
  const setTokens = useAuthStore((s) => s.setTokens);
  const setUser = useAuthStore((s) => s.setUser);
  const router = useRouter();
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => authApi.login(email, password),
    onSuccess: async (data) => {
      setTokens(data.access_token, data.refresh_token ?? null);
      try {
        const user = await authApi.me();
        setUser(user);
      } catch {
        // Non-fatal — /me will be retried by the dashboard shell.
      }
      queryClient.invalidateQueries();
      toast.success("Welcome back.");
      router.push("/dashboard");
    },
    onError: (error) => {
      toast.error(normalizeApiError(error).message);
    },
  });
}

export function useRegister() {
  const router = useRouter();
  return useMutation({
    mutationFn: (payload: UserCreate) => authApi.register(payload),
    onSuccess: () => {
      toast.success("Account created. Sign in to continue.");
      router.push("/login");
    },
    onError: (error) => {
      toast.error(normalizeApiError(error).message);
    },
  });
}

export function useUpdateProfile() {
  const queryClient = useQueryClient();
  const setUser = useAuthStore((s) => s.setUser);
  return useMutation({
    mutationFn: (payload: UserUpdate) => authApi.updateMe(payload),
    onSuccess: (user) => {
      setUser(user);
      queryClient.invalidateQueries({ queryKey: ["me"] });
      toast.success("Profile updated.");
    },
    onError: (error) => toast.error(normalizeApiError(error).message),
  });
}

export function useLogout() {
  const logout = useAuthStore((s) => s.logout);
  const router = useRouter();
  const queryClient = useQueryClient();
  return () => {
    logout();
    queryClient.clear();
    router.push("/login");
  };
}
