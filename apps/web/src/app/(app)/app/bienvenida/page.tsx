"use client";

/**
 * Wizard de primer arranque (DIRECCION_ACTUAL.md "Wizard de primer arranque:
 * pantalla corta de bienvenida con pocos pasos"). Vive bajo `(app)/app/`
 * como cualquier otra pantalla — el `AppShell` (con su barra de navegación
 * completa) sigue visible alrededor, así que el usuario nunca queda
 * atrapado aquí: puede irse a Chat u otra sección en cualquier momento con
 * un clic en la barra lateral, sin que este wizard necesite su propio
 * mecanismo de "salir del todo".
 *
 * Persistencia: `PUT /v1/setup/complete` (`tenants.onboarding_completed_at`,
 * migración 0009) al llegar al paso 4 (completar) o al saltar el paso de
 * MCPs — `(auth)/register` y `(auth)/login` consultan `GET /v1/setup/status`
 * (`onboarding_completed`) para decidir si redirigen aquí en vez de a
 * `/app`. Antes esto vivía en `localStorage` (`edecan_wizard_done`), un flag
 * sin ninguna relación con el tenant/cuenta — un tenant nuevo en un
 * navegador que ya había completado el wizard con OTRA cuenta se lo
 * saltaba entero. Ver HOTFIXES_PENDIENTES.md.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { PasoWizard } from "@/components/configuracion/PasoWizard";
import { SelectorLLM } from "@/components/configuracion/SelectorLLM";
import { CheckIcon } from "@/components/icons";
import { Button } from "@/components/ui";
import { getSetupDetect, putSetupComplete, type SetupDetect } from "@/lib/api-configuracion";

async function marcarOnboardingCompletado(): Promise<void> {
  try {
    await putSetupComplete();
  } catch {
    // No debe bloquear al usuario de seguir a chatear — en el peor caso el
    // wizard vuelve a aparecer la próxima vez que inicie sesión.
  }
}

export default function BienvenidaPage() {
  const router = useRouter();
  const [paso, setPaso] = useState<1 | 2>(1);
  const [detect, setDetect] = useState<SetupDetect | null>(null);
  const [detectLoading, setDetectLoading] = useState(true);

  useEffect(() => {
    let cancelado = false;
    (async () => {
      try {
        const det = await getSetupDetect();
        if (!cancelado) setDetect(det);
      } catch {
        // Una detección local es conveniencia, no un gate del producto. Si
        // la red cambia durante el primer arranque se conserva el selector
        // manual sin lanzar una promesa rechazada ni romper el onboarding.
        if (!cancelado) setDetect(null);
      } finally {
        if (!cancelado) setDetectLoading(false);
      }
    })();
    return () => {
      cancelado = true;
    };
  }, []);

  const irAlChat = useCallback(() => {
    void marcarOnboardingCompletado();
    router.push("/app");
  }, [router]);

  return (
    <div className="mx-auto max-w-xl">
      {paso === 1 && (
        <PasoWizard
          paso={1}
          totalPasos={2}
          titulo="Activa Edecan"
          descripcion="Conéctalo una sola vez. Después solo tendrás que escribirle o hablarle para pedir lo que necesites."
        >
          <SelectorLLM simplified detect={detect} detectLoading={detectLoading} onConnected={() => setPaso(2)} />
          <div className="mt-5 border-t border-slate-100 pt-4 dark:border-slate-800">
            <button
              type="button"
              onClick={() => setPaso(2)}
              className="text-sm font-medium text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200"
            >
              Ahora no, entrar a Edecan
            </button>
            <p className="mt-1 text-xs text-slate-400">
              Podrás activarlo cuando quieras desde Ajustes. Sin una conexión de IA podrá abrirse, pero todavía no responderá.
            </p>
          </div>
        </PasoWizard>
      )}

      {paso === 2 && (
        <PasoWizard
          paso={2}
          totalPasos={2}
          titulo="Todo listo para empezar"
          descripcion="No necesitas aprender menús ni comandos: pídele el resultado en una sola frase."
        >
          <div className="flex flex-col items-center gap-4 py-6 text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-900/40 dark:text-emerald-400">
              <CheckIcon className="h-7 w-7" />
            </span>
            <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
              Por ejemplo: “Organiza mis pendientes, revisa el documento y recuérdame pagar mañana”. Edecan separará el trabajo, te pedirá permiso solo cuando haga falta y te mostrará qué terminó.
            </p>
            <Button size="md" className="w-full justify-center sm:w-auto" onClick={irAlChat}>
              Hablar con Edecan
            </Button>
          </div>
        </PasoWizard>
      )}
    </div>
  );
}
