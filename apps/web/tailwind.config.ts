import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
    "./src/lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      colors: {
        brand: {
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#4f46e5",
          700: "#4338ca",
          800: "#3730a3",
          900: "#312e81",
          950: "#1e1b4b",
        },
        // Escala de grises cálidos de Forge (§8: "sereno", acromático de base).
        // Sustituye los literales `[#d9d9d7]` etc. sueltos por el IDE -- nombres
        // semánticos (superficie/borde/tenue), no valores de marca: el color es
        // un presupuesto escaso, no una paleta decorativa. Cada uno trae su
        // equivalente oscuro explícito porque el modo oscuro NO es el mismo gris
        // con el brillo invertido -- Estado 1 de §3.1 pide "fondo casi negro".
        forja: {
          superficie: "#ffffff",
          "superficie-elevada": "#f7f7f6",
          "superficie-hundida": "#f1f1ef",
          "superficie-oscura": "#0b0b0c",
          "superficie-oscura-elevada": "#19191c",
          "superficie-oscura-hundida": "#000000",
          borde: "#d9d9d7",
          "borde-suave": "#e6e6e4",
          "borde-fuerte": "#cfcfcc",
          "borde-oscuro": "#2b2b2f",
          "borde-oscuro-suave": "#232326",
          tenue: "#8a8a86",
          "tenue-oscuro": "#8f8f96",
        },
      },
      boxShadow: {
        panel: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.15s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
