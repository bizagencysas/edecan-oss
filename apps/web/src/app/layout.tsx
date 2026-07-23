import type { Metadata } from "next";

import { AuthProvider } from "@/lib/auth-context";
import { THEME_INIT_SCRIPT } from "@/lib/theme";

import "./globals.css";

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
    <html lang="es" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-slate-50 font-sans text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-50">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
