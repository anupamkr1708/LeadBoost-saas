"use client";

import { motion } from "framer-motion";
import { Search, ExternalLink, Plus, RotateCw } from "lucide-react";

const DEMO = [
  { name: "Bloom & Sole Footwear", website: "bloomandsole.com", status: "SUCCESS", reason: null },
  { name: "Marina Kicks Co.", website: "marinakicks.in", status: "SUCCESS", reason: null },
  { name: "Heritage Walk Shoes", website: "heritagewalk.co.in", status: "PARTIAL_SUCCESS", reason: "Email not found" },
  { name: "StepFwd Footwear", website: null, status: "FAILED", reason: "No reachable website" },
];

const STATUS_STYLE: Record<string, string> = {
  SUCCESS: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  PARTIAL_SUCCESS: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  FAILED: "bg-rose-500/15 text-rose-300 border-rose-500/30",
};

export function DiscoveryDemo() {
  return (
    <section id="discovery" className="relative py-24">
      <div className="mx-auto max-w-5xl px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-medium uppercase tracking-widest text-primary-400">Lead Discovery</p>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            One search. A list of qualified, verified leads.
          </h2>
          <p className="mt-4 text-muted-foreground">
            Type what you&apos;re looking for the way you&apos;d describe it to a colleague — LeadBoost handles resolving
            websites, deduplicating, and running the full enrichment pipeline.
          </p>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="glass-strong mt-12 overflow-hidden p-2"
        >
          <div className="rounded-xl bg-canvas-charcoal/50 p-6">
            <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] px-5 py-4">
              <Search className="h-5 w-5 text-primary-400" />
              <span className="flex-1 text-sm sm:text-base">Top shoe stores in Mumbai</span>
              <span className="hidden rounded-lg bg-white/[0.06] px-2.5 py-1 text-xs text-muted-foreground sm:inline">Limit: 20</span>
            </div>

            <div className="mt-5 space-y-2.5">
              {DEMO.map((d, i) => (
                <motion.div
                  key={d.name}
                  initial={{ opacity: 0, x: 10 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.4, delay: i * 0.1 }}
                  className="flex flex-col gap-2 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div>
                    <p className="text-sm font-medium">{d.name}</p>
                    <p className="text-xs text-muted-foreground">{d.website ?? d.reason}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[d.status]}`}>
                      {d.status.replace("_", " ")}
                    </span>
                    {d.status === "FAILED" ? (
                      <button className="flex h-7 w-7 items-center justify-center rounded-lg bg-white/[0.06] text-muted-foreground">
                        <RotateCw className="h-3.5 w-3.5" />
                      </button>
                    ) : (
                      <button className="flex h-7 w-7 items-center justify-center rounded-lg bg-white/[0.06] text-muted-foreground">
                        <ExternalLink className="h-3.5 w-3.5" />
                      </button>
                    )}
                    <button className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-500/20 text-primary-300">
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
