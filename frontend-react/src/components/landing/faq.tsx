"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const FAQS = [
  {
    q: "How does natural-language discovery work?",
    a: "Describe the kind of business you're looking for — like \"top shoe stores in Mumbai\" — and LeadBoost searches, resolves each result to a verified website, and runs it through the enrichment pipeline automatically.",
  },
  {
    q: "What happens if a business doesn't have a reachable website?",
    a: "It's reported back with a clear reason instead of silently disappearing, so you always know why a business wasn't added as a lead.",
  },
  {
    q: "Can I add leads manually instead of searching?",
    a: "Yes — you can add a single website or paste a batch of URLs directly, and LeadBoost will run the same enrichment and scoring pipeline on them.",
  },
  {
    q: "How is a lead's score calculated?",
    a: "Each lead is scored using confidence signals from scraping, enrichment, and contact discovery, and assigned a qualification label so your team can prioritize instantly.",
  },
  {
    q: "Can I cancel or change plans anytime?",
    a: "Yes — you can upgrade, downgrade, or cancel your subscription at any time from the billing page, with the option to cancel immediately or at the end of your cycle.",
  },
];

export function Faq() {
  const [openIndex, setOpenIndex] = useState<number | null>(0);

  return (
    <section id="faq" className="relative py-24">
      <div className="mx-auto max-w-3xl px-4 sm:px-6 lg:px-8">
        <div className="text-center">
          <p className="text-xs font-medium uppercase tracking-widest text-primary-400">FAQ</p>
          <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">Questions, answered</h2>
        </div>

        <div className="mt-12 space-y-3">
          {FAQS.map((item, i) => {
            const isOpen = openIndex === i;
            return (
              <div key={item.q} className="glass overflow-hidden">
                <button
                  onClick={() => setOpenIndex(isOpen ? null : i)}
                  className="flex w-full items-center justify-between gap-4 px-6 py-5 text-left"
                  aria-expanded={isOpen}
                >
                  <span className="font-display text-sm font-semibold sm:text-base">{item.q}</span>
                  <ChevronDown className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", isOpen && "rotate-180")} />
                </button>
                {isOpen && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    transition={{ duration: 0.25 }}
                    className="overflow-hidden"
                  >
                    <p className="px-6 pb-5 text-sm text-muted-foreground">{item.a}</p>
                  </motion.div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
