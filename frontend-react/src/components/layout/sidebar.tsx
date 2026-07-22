"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Sparkles,
  Users,
  GitBranch,
  Mail,
  BarChart3,
  Building2,
  CreditCard,
  UserCircle,
  Settings,
  Zap,
} from "lucide-react";
import { NAV_ITEMS } from "@/lib/constants";
import { cn } from "@/lib/utils";

const ICONS: Record<string, typeof LayoutDashboard> = {
  LayoutDashboard,
  Sparkles,
  Users,
  GitBranch,
  Mail,
  BarChart3,
  Building2,
  CreditCard,
  UserCircle,
  Settings,
};

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-white/[0.06] bg-white/[0.015] backdrop-blur-2xl lg:flex">
      <div className="flex h-16 items-center gap-2.5 px-6">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary-400 to-fuchsia-500 shadow-glow">
          <Zap className="h-4 w-4 text-white" fill="white" />
        </div>
        <span className="font-display text-lg font-semibold tracking-tight">LeadBoost</span>
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-4">
        {NAV_ITEMS.map((item) => {
          const Icon = ICONS[item.icon] ?? LayoutDashboard;
          const active = pathname === item.href || pathname?.startsWith(item.href + "/");
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-200",
                active
                  ? "bg-white/[0.07] text-foreground shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]"
                  : "text-muted-foreground hover:bg-white/[0.04] hover:text-foreground"
              )}
            >
              {active && (
                <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-full bg-gradient-to-b from-primary-400 to-fuchsia-500" />
              )}
              <Icon className={cn("h-4 w-4 shrink-0", active ? "text-primary-300" : "text-muted-foreground group-hover:text-foreground")} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mx-3 mb-4 rounded-2xl border border-white/10 bg-gradient-to-br from-primary-500/10 to-fuchsia-500/5 p-4">
        <p className="font-display text-sm font-semibold">Upgrade to Pro</p>
        <p className="mt-1 text-xs text-muted-foreground">Unlock unlimited discovery searches and AI enrichment.</p>
        <Link
          href="/billing"
          className="mt-3 inline-flex h-8 items-center justify-center rounded-lg bg-white/10 px-3 text-xs font-medium hover:bg-white/[0.16]"
        >
          View plans
        </Link>
      </div>
    </aside>
  );
}
