"use client";

import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";

import { PhoneIcon } from "@/components/icons";
import { Alert, Badge, Button, Card, CardBody, CardHeader } from "@/components/ui";
import { createDevicePairing } from "@/lib/api";
import {
  formatPairingTimeLeft,
  pairingExpiryMs,
  pairingSecondsLeft,
  type DevicePairingOut,
} from "@/lib/device-pairing";

export function PhonePairingCard() {
  const [pairing, setPairing] = useState<DevicePairingOut | null>(null);
  const [expiresAtMs, setExpiresAtMs] = useState(0);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pairing || expiresAtMs <= 0) return;
    const update = () => setSecondsLeft(pairingSecondsLeft(expiresAtMs));
    update();
    const interval = window.setInterval(update, 1000);
    return () => window.clearInterval(interval);
  }, [expiresAtMs, pairing]);

  async function generatePairing() {
    setBusy(true);
    setError(null);
    try {
      const created = await createDevicePairing();
      if (!created.pairing_uri) throw new Error("El servidor no devolvió un QR válido.");
      const expiry = pairingExpiryMs(created);
      setPairing(created);
      setExpiresAtMs(expiry);
      setSecondsLeft(pairingSecondsLeft(expiry));
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo preparar la conexión.");
    } finally {
      setBusy(false);
    }
  }

  const expired = pairing !== null && secondsLeft === 0;

  return (
    <Card className="overflow-hidden border-brand-200 bg-gradient-to-br from-white to-brand-50/50 dark:border-brand-900 dark:from-slate-900 dark:to-brand-950/20">
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <PhoneIcon className="h-4 w-4 text-brand-600 dark:text-brand-400" />
            Conectar mi teléfono
          </span>
        }
        description="Usa la Cámara de tu teléfono y toca el aviso de Edecan. No tienes que copiar enlaces, códigos ni contraseñas."
        actions={
          <Badge variant={pairing && !expired ? "success" : "neutral"}>
            {pairing && !expired ? "QR listo" : expired ? "QR vencido" : "Falta conectar"}
          </Badge>
        }
      />
      <CardBody>
        {error && (
          <div className="mb-4">
            <Alert variant="error">{error}</Alert>
          </div>
        )}

        <div className="grid items-center gap-5 sm:grid-cols-[minmax(0,1fr)_auto]">
          <div className="space-y-3">
            <ol className="space-y-2 text-sm leading-6 text-slate-600 dark:text-slate-300">
              <li><span className="font-semibold text-slate-900 dark:text-slate-100">1.</span> Abre la Cámara de tu teléfono.</li>
              <li><span className="font-semibold text-slate-900 dark:text-slate-100">2.</span> Apunta a este código QR.</li>
              <li><span className="font-semibold text-slate-900 dark:text-slate-100">3.</span> Toca el aviso de Edecan que aparece.</li>
            </ol>

            <div aria-live="polite" className="text-sm text-slate-500 dark:text-slate-400">
              {pairing && !expired
                ? <>Este QR vence en <strong className="font-semibold text-slate-800 dark:text-slate-100">{formatPairingTimeLeft(secondsLeft)}</strong>.</>
                : expired
                  ? "Este QR venció. Genera uno nuevo para continuar."
                  : "Genera un QR temporal cuando tengas el teléfono a mano."}
            </div>

            <Button onClick={() => void generatePairing()} loading={busy}>
              {pairing ? "Generar un QR nuevo" : "Mostrar código QR"}
            </Button>
          </div>

          {pairing && !expired && (
            <div className="mx-auto rounded-2xl border border-slate-200 bg-white p-3 shadow-sm dark:border-slate-700">
              <QRCodeSVG
                value={pairing.pairing_uri}
                size={184}
                level="M"
                marginSize={3}
                title="Código QR temporal para conectar tu teléfono con Edecan"
              />
            </div>
          )}
        </div>
      </CardBody>
    </Card>
  );
}
