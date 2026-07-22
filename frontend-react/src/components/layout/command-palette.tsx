"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
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
  Search,
} from "lucide-react";
import { NAV_ITEMS } from "@/lib/constants";
import { useKeyboardShortcut } from "@/hooks/use-keyboard-shortcut";

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

/** Global Cmd/Ctrl+K command palette for fast navigation across LeadBoost. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();

  useKeyboardShortcut({ key: "k", metaOrCtrl: true, onTrigger: () => setOpen((o) => !o) });

  useEffect(() => {
    const openHandler = () => setOpen(true);
    window.addEventListener("leadboost:open-command-palette", openHandler);
    return () => window.removeEventListener("leadboost:open-command-palette", openHandler);
  }, []);

  useEffect(() => {
    if (!open) return;
    function onEsc(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onEsc);
    return () => window.removeEventListener("keydown", onEsc);
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center bg-black/70 pt-[15vh] backdrop-blur-sm" onClick={() => setOpen(false)}>
      <div
        className="w-full max-w-lg overflow-hidden rounded-2xl border border-white/10 bg-canvas-midnight/95 shadow-elevated backdrop-blur-2xl animate-in fade-in-0 zoom-in-95"
        onClick={(e) => e.stopPropagation()}
      >
        <Command label="Command Menu" className="flex flex-col">
          <div className="flex items-center gap-2 border-b border-white/10 px-4">
            <Search className="h-4 w-4 text-muted-foreground" />
            <Command.Input
              autoFocus
              placeholder="Jump to a page, search leads..."
              className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/70"
            />
            <span className="kbd">esc</span>
          </div>
          <Command.List className="max-h-80 overflow-y-auto p-2">
            <Command.Empty className="py-8 text-center text-sm text-muted-foreground">No results found.</Command.Empty>
            <Command.Group heading="Navigate" className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
              {NAV_ITEMS.map((item) => {
                const Icon = ICONS[item.icon] ?? LayoutDashboard;
                return (
                  <Command.Item
                    key={item.href}
                    onSelect={() => {
                      router.push(item.href);
                      setOpen(false);
                    }}
                    className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2.5 text-sm text-foreground data-[selected=true]:bg-white/10"
                  >
                    <Icon className="h-4 w-4 text-muted-foreground" />
                    {item.label}
                  </Command.Item>
                );
              })}
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>
  );
}
