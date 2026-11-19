/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#111827",
        mist: "#f6f7fb",
        signal: "#2563eb",
      },
    },
  },
  plugins: [],
};
