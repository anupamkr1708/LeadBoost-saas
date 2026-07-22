import Link from "next/link";
import { Zap } from "lucide-react";

export function Footer() {
  return (
    <footer className="border-t border-white/[0.06] py-12">
      <div className="mx-auto flex max-w-7xl flex-col items-center justify-between gap-6 px-4 sm:px-6 md:flex-row lg:px-8">
        <Link href="/" className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-primary-400 to-fuchsia-500">
            <Zap className="h-3.5 w-3.5 text-white" fill="white" />
          </div>
          <span className="font-display text-sm font-semibold">LeadBoost</span>
        </Link>
        <p className="text-xs text-muted-foreground">© {new Date().getFullYear()} LeadBoost. All rights reserved.</p>
        <div className="flex items-center gap-6 text-xs text-muted-foreground">
          <a href="#features" className="hover:text-foreground">Features</a>
          <a href="#pricing" className="hover:text-foreground">Pricing</a>
          <a href="#faq" className="hover:text-foreground">FAQ</a>
        </div>
      </div>
    </footer>
  );
}
