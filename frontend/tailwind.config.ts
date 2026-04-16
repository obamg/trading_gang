import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        canvas: "#070C18",
        bgPrimary: "#0B1120",
        bgSecondary: "#111827",
        bgCard: "#1A2235",
        bgElevated: "#1F2D42",
        bgHover: "#243350",
        primary: {
          400: "#60A5FA",
          500: "#3B82F6",
          600: "#2563EB",
          subtle: "#1E3A5F",
        },
        profit: { DEFAULT: "#10B981", subtle: "#064E3B" },
        loss: { DEFAULT: "#EF4444", subtle: "#450A0A" },
        warning: { DEFAULT: "#F59E0B", subtle: "#451A03" },
        neutral: "#64748B",
        textPrimary: "#F1F5F9",
        textSecondary: "#94A3B8",
        textMuted: "#64748B",
        textDisabled: "#374151",
        borderSubtle: "#1E293B",
        borderDefault: "#334155",
        borderStrong: "#475569",
        // Module accents
        "mod-radarx": "#3B82F6",
        "mod-whaleradar": "#06B6D4",
        "mod-liquidmap": "#F97316",
        "mod-sentimentpulse": "#A855F7",
        "mod-macropulse": "#6366F1",
        "mod-gemradar": "#10B981",
        "mod-riskcalc": "#EAB308",
        "mod-tradelog": "#94A3B8",
        "mod-performancecore": "#14B8A6",
        "mod-oracle": "#8B5CF6",
      },
      fontFamily: {
        ui: ["Inter", "-apple-system", "BlinkMacSystemFont", "sans-serif"],
        data: ["'JetBrains Mono'", "'Fira Code'", "monospace"],
      },
      fontSize: {
        xs: ["11px", { lineHeight: "1.4" }],
        sm: ["13px", { lineHeight: "1.5" }],
        base: ["14px", { lineHeight: "1.6" }],
        md: ["16px", { lineHeight: "1.5" }],
        lg: ["18px", { lineHeight: "1.4" }],
        xl: ["22px", { lineHeight: "1.3" }],
        "2xl": ["28px", { lineHeight: "1.2" }],
        "3xl": ["36px", { lineHeight: "1.1" }],
      },
      borderRadius: { sm: "4px", md: "8px", lg: "12px", xl: "16px" },
      boxShadow: {
        card: "0 4px 24px rgba(0,0,0,0.3)",
        glow: "0 0 0 3px rgba(59,130,246,0.15)",
      },
      keyframes: {
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        pulseDot: {
          "0%,100%": { transform: "scale(1)", opacity: "1" },
          "50%": { transform: "scale(1.3)", opacity: "0.7" },
        },
        slideDown: {
          "0%": { transform: "translateY(-8px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
      animation: {
        shimmer: "shimmer 1.5s linear infinite",
        pulseDot: "pulseDot 2s ease-in-out infinite",
        slideDown: "slideDown 300ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
