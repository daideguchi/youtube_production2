/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        background: "#f6f7fb",
        foreground: "#1f2937",
        shell: {
          surface: "#ffffff",
          border: "#e2e8f0",
          accent: "#0f172a",
          subtle: "#475569",
        },
      },
      fontFamily: {
        sans: ['"Noto Sans JP"', '"Space Grotesk"', "system-ui", "sans-serif"],
      },
      boxShadow: {
        panel: "0 6px 12px rgba(15, 23, 42, 0.08)",
      },
      borderRadius: {
        panel: "18px",
      },
    },
  },
  plugins: [],
};
