import Link from "next/link";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AmbientBackground } from "@/components/shared/ambient-background";

export default function NotFound() {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <AmbientBackground />
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary-500/15 text-primary-300">
        <Compass className="h-6 w-6" />
      </div>
      <h1 className="font-display text-3xl font-semibold tracking-tight">Page not found</h1>
      <p className="max-w-sm text-sm text-muted-foreground">The page you&apos;re looking for doesn&apos;t exist or may have moved.</p>
      <Button asChild>
        <Link href="/dashboard">Back to dashboard</Link>
      </Button>
    </div>
  );
}
