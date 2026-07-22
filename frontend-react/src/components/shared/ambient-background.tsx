"use client";

/**
 * The signature ambient backdrop used across the landing page and the app shell:
 * layered, slowly-drifting radial gradients (violet / magenta / indigo / amber)
 * over a deep charcoal canvas, with a faint masked grid texture on top.
 * Fixed + pointer-events-none so it never interferes with scrolling or clicks.
 */
export function AmbientBackground() {
  return (
    <div className="ambient-bg" aria-hidden="true">
      <div
        className="ambient-blob left-[-10%] top-[-15%] h-[560px] w-[560px] bg-primary-600/30 animate-drift"
        style={{ animationDelay: "0s" }}
      />
      <div
        className="ambient-blob right-[-15%] top-[5%] h-[620px] w-[620px] bg-fuchsia-600/20 animate-drift-slow"
        style={{ animationDelay: "-8s" }}
      />
      <div
        className="ambient-blob bottom-[-20%] left-[15%] h-[520px] w-[520px] bg-indigo-600/25 animate-drift"
        style={{ animationDelay: "-4s" }}
      />
      <div
        className="ambient-blob bottom-[-10%] right-[10%] h-[420px] w-[420px] bg-amber-500/[0.08] animate-drift-slow"
        style={{ animationDelay: "-14s" }}
      />
      <div className="grid-overlay" />
    </div>
  );
}
