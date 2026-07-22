"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { normalizeApiError } from "@/lib/api-client";
import { discoveryApi } from "@/features/discovery/api";
import type { DiscoverySearchRequest } from "@/types/api";

export function useDiscoverySearch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ signal, ...payload }: DiscoverySearchRequest & { signal?: AbortSignal }) =>
      discoveryApi.search(payload, { signal }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
  });
}

export { normalizeApiError };
