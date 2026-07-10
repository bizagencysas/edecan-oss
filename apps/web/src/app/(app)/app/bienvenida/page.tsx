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

import { CardServidoresMcp } from "@/components/configuracion/CardServidoresMcp";
import { PasoWizard } from "@/components/configuracion/PasoWizard";
import { SelectorLLM } from "@/components/configuracion/SelectorLLM";
import { SelectorVoz } from "@/components/configuracion/SelectorVoz";
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
  const [paso, setPaso] = useState<1 | 2 | 3 | 4>(1);
  const [detect, setDetect] = useState<SetupDetect | null>(null);
  const [detectLoading, setDetectLoading] = useState(true);
  const [vozConectada, setVozConectada] = useState(false);
  const [mcpConectado, setMcpConectado] = useState(false);

  useEffect(() => {
    let cancelado = false;
    (async () => {
      try {
        const det = await getSetupDetect();
        if (!cancelado) setDetect(det);
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

  function saltarMcp() {
    void marcarOnboardingCompletado();
    setPaso(4);
  }

  return (
    <div className="mx-auto max-w-xl">
      {paso === 1 && (
        <PasoWizard
          paso={1}
          totalPasos={4}
          titulo="Conecta tu inteligencia"
          descripcion="Elige cómo quieres que Edecán piense: usa lo que ya tienes instalado o pega una API key."
        >
          <SelectorLLM detect={detect} detectLoading={detectLoading} onConnected={() => setPaso(2)} />
          <p className="mt-4 text-xs text-slate-400">
            ¿Prefieres hacerlo después?{" "}
            <button
              type="button"
              onClick={() => setPaso(2)}
              className="font-medium text-brand-600 hover:text-brand-700 dark:text-brand-400"
            >
              Seguir sin conectar
            </button>{" "}
            — puedes hacerlo cuando quieras desde Configuración.
          </p>
        </PasoWizard>
      )}

      {paso === 2 && (
        <PasoWizard
          paso={2}
          totalPasos={4}
          titulo="Voz"
          descripcion="Transcripción y síntesis de voz — opcional, puedes saltarte este paso."
        >
          <SelectorVoz
            localMode={detect?.local_mode === true}
            onSttConnected={() => setVozConectada(true)}
            onTtsConnected={() => setVozConectada(true)}
          />
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setPaso(3)}>
              Saltar
            </Button>
            {vozConectada && (
              <Button onClick={() => setPaso(3)}>Continuar</Button>
            )}
          </div>
        </PasoWizard>
      )}

      {paso === 3 && (
        <PasoWizard
          paso={3}
          totalPasos={4}
          titulo="MCPs manuales"
          descripcion="Conecta tus propios servidores MCP (Model Context Protocol) — opcional, puedes saltarte este paso."
        >
          <CardServidoresMcp
            localMode={detect?.local_mode === true}
            hideHeader
            onServerConnected={() => setMcpConectado(true)}
          />
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="secondary" onClick={saltarMcp}>
              Saltar
            </Button>
            {mcpConectado && (
              <Button
                onClick={() => {
                  void marcarOnboardingCompletado();
                  setPaso(4);
                }}
              >
                Continuar
              </Button>
            )}
          </div>
        </PasoWizard>
      )}

      {paso === 4 && (
        <PasoWizard paso={4} totalPasos={4} titulo="¡Listo!" descripcion="Edecán está listo para trabajar contigo.">
          <div className="flex flex-col items-center gap-4 py-6 text-center">
            <span className="flex h-14 w-14 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-900/40 dark:text-emerald-400">
              <CheckIcon className="h-7 w-7" />
            </span>
            <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
              Puedes conectar el resto (teléfono, correo, calendario, redes…) cuando quieras desde Configuración —
              nada de eso es obligatorio para empezar a chatear.
            </p>
            <Button size="md" className="w-full justify-center sm:w-auto" onClick={irAlChat}>
              Empezar a chatear
            </Button>
          </div>
        </PasoWizard>
      )}
    </div>
  );
}
