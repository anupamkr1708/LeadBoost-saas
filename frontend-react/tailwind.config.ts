import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "1.5rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        display: ["var(--font-space-grotesk)", "var(--font-inter)", "system-ui", "sans-serif"],
        mono: ["var(--font-jetbrains-mono)", "ui-monospace", "monospace"],
      },
      colors: {
        canvas: {
          DEFAULT: "#07060B",
          charcoal: "#0D0B14",
          midnight: "#12101C",
        },
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "#8B5CF6",
          50: "#F5F3FF",
          400: "#A78BFA",
          500: "#8B5CF6",
          600: "#7C3AED",
          700: "#6D28D9",
        },
        accent: {
          DEFAULT: "#38BDF8",
          pink: "#D946EF",
          indigo: "#6366F1",
        },
        success: { DEFAULT: "#10B981", muted: "#064E3B" },
        warning: { DEFAULT: "#F59E0B", muted: "#451A03" },
        danger: { DEFAULT: "#F43F5E", muted: "#4C0519" },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "#A3A0B8",
        },
        glass: {
          surface: "rgba(255,255,255,0.045)",
          border: "rgba(255,255,255,0.09)",
          highlight: "rgba(255,255,255,0.14)",
        },
        card: { DEFAULT: "hsl(var(--card))", foreground: "hsl(var(--card-foreground))" },
        popover: { DEFAULT: "hsl(var(--popover))", foreground: "hsl(var(--popover-foreground))" },
        destructive: { DEFAULT: "#F43F5E", foreground: "#FFF1F2" },
        secondary: { DEFAULT: "hsl(var(--secondary))", foreground: "hsl(var(--secondary-foreground))" },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "1.25rem",
        "2xl": "1.75rem",
        "3xl": "2.25rem",
      },
      boxShadow: {
        glass: "0 1px 1px rgba(255,255,255,0.06) inset, 0 8px 32px rgba(0,0,0,0.45)",
        glow: "0 0 0 1px rgba(139,92,246,0.3), 0 0 40px rgba(139,92,246,0.25)",
        "glow-pink": "0 0 0 1px rgba(217,70,239,0.25), 0 0 40px rgba(217,70,239,0.2)",
        elevated: "0 20px 60px -15px rgba(0,0,0,0.6)",
      },
      backgroundImage: {
        "grid-texture":
          "linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px)",
      },
      keyframes: {
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
        "accordion-up": { from: { height: "var(--radix-accordion-content-height)" }, to: { height: "0" } },
        drift: {
          "0%, 100%": { transform: "translate(0, 0) scale(1)" },
          "33%": { transform: "translate(3%, -4%) scale(1.05)" },
          "66%": { transform: "translate(-2%, 3%) scale(0.98)" },
        },
        "drift-slow": {
          "0%, 100%": { transform: "translate(0,0) scale(1)" },
          "50%": { transform: "translate(-4%, 4%) scale(1.08)" },
        },
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
        scan: { "0%": { transform: "translateX(-100%)" }, "100%": { transform: "translateX(100%)" } },
        "fade-up": { "0%": { opacity: "0", transform: "translateY(12px)" }, "100%": { opacity: "1", transform: "translateY(0)" } },
        "pulse-glow": { "0%, 100%": { opacity: "0.6" }, "50%": { opacity: "1" } },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        drift: "drift 22s ease-in-out infinite",
        "drift-slow": "drift-slow 30s ease-in-out infinite",
        shimmer: "shimmer 2.5s linear infinite",
        scan: "scan 1.6s ease-in-out infinite",
        "fade-up": "fade-up 0.5s cubic-bezier(0.16,1,0.3,1) both",
        "pulse-glow": "pulse-glow 2.4s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
export default config;
