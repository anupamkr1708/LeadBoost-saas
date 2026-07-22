"use client";

import { motion } from "framer-motion";
import { Search, Globe, Sparkles, Gauge, CheckCircle2 } from "lucide-react";

const STAGES = [
  { icon: Search, label: "Discover", desc: "Natural-language search finds real businesses" },
  { icon: Globe, label: "Resolve", desc: "Websites are verified as live and reachable" },
  { icon: Sparkles, label: "Enrich", desc: "Company, contact, and firmographic data extracted" },
  { icon: Gauge, label: "Score", desc: "Each lead is qualified with a confidence-backed score" },
  { icon: CheckCircle2, label: "Ready", desc: "Outreach draft generated, lead ready to work" },
];

export function PipelineVisualization() {
  return (
    <section className="relative py-24">
      <div className="mx-auto max-w-6xl px-4 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-medium uppercase tracking-widest text-primary-400">The pipeline</p>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            From a query to a qualified lead, automatically
          </h2>
        </div>

        <div className="relative mt-16">
          <div className="absolute left-0 right-0 top-6 hidden h-px bg-gradient-to-r from-transparent via-white/15 to-transparent md:block" />
          <div className="grid gap-8 md:grid-cols-5">
            {STAGES.map((s, i) => (
              <motion.div
                key={s.label}
                initial={{ opacity: 0, y: 16 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, margin: "-60px" }}
                transition={{ duration: 0.5, delay: i * 0.1 }}
                className="relative flex flex-col items-center text-center md:items-start md:text-left"
              >
                <div className="relative z-10 flex h-12 w-12 items-center justify-center rounded-2xl border border-white/10 bg-canvas-charcoal text-primary-300 shadow-glow">
                  <s.icon className="h-5 w-5" />
                </div>
                <p className="mt-4 font-display text-sm font-semibold">
                  <span className="font-mono text-primary-400">0{i + 1}</span> {s.label}
                </p>
                <p className="mt-1.5 text-xs text-muted-foreground">{s.desc}</p>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
