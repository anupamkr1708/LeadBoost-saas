import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}

/** Consistent empty-state treatment: an invitation to act, not a dead end. */
export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-white/10 bg-white/[0.02] px-6 py-16 text-center", className)}>
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-white/[0.05] text-primary-400">
        <Icon className="h-6 w-6" />
      </div>
      <div className="space-y-1">
        <p className="font-display text-base font-semibold">{title}</p>
        {description && <p className="max-w-sm text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}
