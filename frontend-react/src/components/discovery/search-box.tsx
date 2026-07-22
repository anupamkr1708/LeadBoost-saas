"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Search, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { discoverySearchSchema, type DiscoverySearchValues } from "@/lib/validation";

const EXAMPLES = ["Top shoe stores in Mumbai", "Boutique hotels in Lisbon", "Dentists in Bangalore", "SaaS startups in Austin"];

interface SearchBoxProps {
  onSearch: (values: DiscoverySearchValues) => void;
  loading?: boolean;
}

/** The flagship natural-language discovery search bar. */
export function SearchBox({ onSearch, loading }: SearchBoxProps) {
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors },
  } = useForm<DiscoverySearchValues>({
    resolver: zodResolver(discoverySearchSchema),
    defaultValues: { limit: 20 },
  });

  const limit = watch("limit");

  return (
    <div className="glass-strong relative overflow-hidden p-2 shadow-elevated">
      {loading && <span className="scanline pointer-events-none absolute inset-y-0 z-10 animate-scan" />}
      <form onSubmit={handleSubmit(onSearch)} className="relative flex flex-col gap-3 rounded-xl bg-canvas-charcoal/40 p-3 sm:flex-row sm:items-center">
        <div className="flex flex-1 items-center gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3.5 focus-within:ring-2 focus-within:ring-primary-400">
          <Sparkles className="h-5 w-5 shrink-0 text-primary-400" />
          <input
            {...register("query")}
            placeholder="Describe who you're looking for — e.g. Top shoe stores in Mumbai"
            className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground/70 focus:outline-none sm:text-base"
          />
        </div>

        <div className="flex items-center gap-2">
          <Select value={String(limit ?? 20)} onValueChange={(v) => setValue("limit", Number(v))}>
            <SelectTrigger className="w-28 shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[10, 20, 30, 50].map((n) => (
                <SelectItem key={n} value={String(n)}>
                  Limit: {n}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="submit" size="lg" loading={loading} className="shrink-0">
            <Search className="h-4 w-4" />
            Search
          </Button>
        </div>
      </form>
      {errors.query && <p className="px-4 pb-2 pt-1 text-xs text-rose-400">{errors.query.message}</p>}

      <div className="flex flex-wrap gap-2 px-4 pb-3 pt-1">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => setValue("query", ex, { shouldValidate: true })}
            className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs text-muted-foreground transition-colors hover:bg-white/[0.07] hover:text-foreground"
          >
            {ex}
          </button>
        ))}
      </div>
    </div>
  );
}
