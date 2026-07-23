"use client";

import { useEffect } from "react";

import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button, Card, CardBody } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  const { error, isAuthenticated, isLocalDesktop, loading, refresh } = useAuth();

  useEffect(() => {
    if (!loading && isAuthenticated) {
      window.location.replace("/app/");
    }
  }, [loading, isAuthenticated]);

  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center bg-gradient-to-b from-brand-50 to-white px-4 py-12 dark:from-slate-950 dark:to-slate-950">
      <ThemeToggle className="absolute right-4 top-4 rounded-md p-2 text-slate-500 hover:bg-white/60 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-slate-900/60 dark:hover:text-white" />
      <Logo className="mb-8" markClassName="h-10 w-10" wordClassName="text-xl" />
      <div className="w-full max-w-sm">
        {loading ? (
          <Card>
            <CardBody className="text-center text-sm text-slate-500 dark:text-slate-400">
              Abriendo tu Edecán…
            </CardBody>
          </Card>
        ) : isLocalDesktop ? (
          <Card>
            <CardBody className="space-y-4 text-center">
              {isAuthenticated ? (
                <p className="text-sm text-slate-500 dark:text-slate-400">Abriendo tu Edecán…</p>
              ) : (
                <>
                  <div>
                    <h1 className="text-lg font-semibold text-slate-900 dark:text-slate-50">
                      No pudimos abrir tu Edecán
                    </h1>
                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                      No necesitas correo ni contraseña. Vuelve a intentar la conexión con esta computadora.
                    </p>
                  </div>
                  {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
                  <Button className="w-full" onClick={() => void refresh()}>
                    Volver a intentar
                  </Button>
                </>
              )}
            </CardBody>
          </Card>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
