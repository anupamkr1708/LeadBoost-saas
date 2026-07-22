"use client";

import Link from "next/link";
import { Search, Bell, LogOut, UserCircle, Settings } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useAuthStore } from "@/store/auth-store";
import { useLogout } from "@/features/auth/hooks";
import { getInitials } from "@/lib/utils";
import { MobileNav } from "@/components/layout/mobile-nav";

export function Topbar() {
  const user = useAuthStore((s) => s.user);
  const logout = useLogout();

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-4 border-b border-white/[0.06] bg-canvas/70 px-4 backdrop-blur-xl sm:px-6">
      <MobileNav />
      <button
        onClick={() => window.dispatchEvent(new CustomEvent("leadboost:open-command-palette"))}
        className="flex h-9 flex-1 max-w-md items-center gap-2.5 rounded-xl border border-white/10 bg-white/[0.03] px-3.5 text-sm text-muted-foreground transition-colors hover:bg-white/[0.05]"
      >
        <Search className="h-4 w-4" />
        <span className="flex-1 text-left">Search leads, pages, actions…</span>
        <span className="kbd">⌘K</span>
      </button>

      <div className="ml-auto flex items-center gap-3">
        <button className="relative flex h-9 w-9 items-center justify-center rounded-xl text-muted-foreground transition-colors hover:bg-white/[0.05] hover:text-foreground">
          <Bell className="h-[18px] w-[18px]" />
          <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-primary-400" />
        </button>

        <DropdownMenu>
          <DropdownMenuTrigger className="flex items-center gap-2 rounded-xl p-1 pr-2 transition-colors hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400">
            <Avatar className="h-8 w-8">
              <AvatarFallback>{getInitials(`${user?.first_name ?? ""} ${user?.last_name ?? ""}`, user?.email)}</AvatarFallback>
            </Avatar>
            <span className="hidden text-sm font-medium sm:inline">{user?.first_name ?? user?.email ?? "Account"}</span>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel>{user?.email}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <Link href="/profile" className="flex w-full items-center gap-2">
                <UserCircle className="h-4 w-4" /> Profile
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/settings" className="flex w-full items-center gap-2">
                <Settings className="h-4 w-4" /> Settings
              </Link>
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem destructive onClick={logout}>
              <LogOut className="h-4 w-4" /> Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
