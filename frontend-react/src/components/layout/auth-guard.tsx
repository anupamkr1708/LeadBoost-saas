"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth-store";
import { useCurrentUser } from "@/features/auth/hooks";
import { Loader2 } from "lucide-react";

/**
 * Client-side route protection. Tokens live in localStorage (via zustand persist),
 * not cookies, so this guard — not Next.js middleware — is the enforcement point.
 * Waits for the persisted store to hydrate before deciding, to avoid flashing
 * a redirect on a hard refresh.
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const hasHydrated = useAuthStore((s) => s.hasHydrated);
  const accessToken = useAuthStore((s) => s.accessToken);
  useCurrentUser();

  useEffect(() => {
    if (hasHydrated && !accessToken) {
      router.replace("/login");
    }
  }, [hasHydrated, accessToken, router]);

  if (!hasHydrated || !accessToken) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas">
        <Loader2 className="h-6 w-6 animate-spin text-primary-400" />
      </div>
    );
  }

  return <>{children}</>;
}
