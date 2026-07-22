import { Badge } from "@/components/ui/badge";
import { QUALIFICATION_STYLES, DEFAULT_QUALIFICATION_STYLE, STATUS_STYLES } from "@/lib/constants";
import { cn } from "@/lib/utils";

/** Renders a pipeline/discovery status ("SUCCESS", "FAILED", ...) with a matching dot + color. */
export function StatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge variant="outline">Unknown</Badge>;
  const style = STATUS_STYLES[status.toUpperCase()] ?? {
    label: status,
    className: "bg-white/10 text-muted-foreground border-white/10",
    dot: "bg-white/40",
  };
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium", style.className)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", style.dot)} />
      {style.label}
    </span>
  );
}

/** Renders a lead qualification_label ("hot", "warm", ...) with a matching color treatment. */
export function QualificationBadge({ label }: { label: string | null | undefined }) {
  const key = label?.toLowerCase() ?? "";
  const style = QUALIFICATION_STYLES[key] ?? { ...DEFAULT_QUALIFICATION_STYLE, label: label || "Unscored" };
  return <span className={cn("inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium", style.className)}>{style.label}</span>;
}
