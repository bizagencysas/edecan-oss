import type { Metadata } from "next";
import { Baloo_2 } from "next/font/google";

import { AuthProvider } from "@/lib/auth-context";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

import "./globals.css";

// Tipografía del wordmark "edecán" (`components/Logo.tsx`), igual que el
// sitio de marketing (`Documents/edecan/src/app/layout.tsx`).
const wordmarkFont = Baloo_2({
  variable: "--font-wordmark",
  subsets: ["latin"],
  weight: ["800"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Edecán",
  description: "Tu asistente personal para conversar, crear, organizar y hacer cosas desde cualquier dispositivo.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // `suppressHydrationWarning` porque el script de abajo agrega la clase
    // "dark" a <html> antes de que React hidrate (ver lib/theme.ts) — sin
    // esto React reportaría un mismatch server/cliente en ese atributo.
    <html lang="es" suppressHydrationWarning className={wordmarkFont.variable}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-slate-50 font-sans text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-50">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
