import Link from "next/link";
import { Zap } from "lucide-react";
import { AmbientBackground } from "@/components/shared/ambient-background";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center px-4 py-12">
      <AmbientBackground />
      <Link href="/" className="mb-8 flex items-center gap-2.5">
        <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 to-fuchsia-500 shadow-glow">
          <Zap className="h-[18px] w-[18px] text-white" fill="white" />
        </div>
        <span className="font-display text-xl font-semibold tracking-tight">LeadBoost</span>
      </Link>
      <div className="w-full max-w-md">{children}</div>
    </div>
  );
}
