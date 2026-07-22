"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AmbientBackground } from "@/components/shared/ambient-background";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <AmbientBackground />
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-rose-500/15 text-rose-400">
        <AlertTriangle className="h-6 w-6" />
      </div>
      <h1 className="font-display text-3xl font-semibold tracking-tight">Something went wrong</h1>
      <p className="max-w-sm text-sm text-muted-foreground">An unexpected error occurred. You can try again, or head back to the dashboard.</p>
      <Button onClick={() => reset()}>Try again</Button>
    </div>
  );
}
