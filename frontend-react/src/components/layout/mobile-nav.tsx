"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, Zap } from "lucide-react";
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
} from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
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

export function MobileNav() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger className="flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground hover:bg-white/[0.05] lg:hidden">
        <Menu className="h-5 w-5" />
      </SheetTrigger>
      <SheetContent side="left" className="max-w-xs p-0">
        <SheetHeader className="mb-2">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary-400 to-fuchsia-500">
              <Zap className="h-4 w-4 text-white" fill="white" />
            </div>
            <SheetTitle>LeadBoost</SheetTitle>
          </div>
        </SheetHeader>
        <nav className="flex-1 space-y-0.5 px-4 py-2">
          {NAV_ITEMS.map((item) => {
            const Icon = ICONS[item.icon] ?? LayoutDashboard;
            const active = pathname === item.href || pathname?.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium",
                  active ? "bg-white/[0.08] text-foreground" : "text-muted-foreground hover:bg-white/[0.04]"
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </SheetContent>
    </Sheet>
  );
}
