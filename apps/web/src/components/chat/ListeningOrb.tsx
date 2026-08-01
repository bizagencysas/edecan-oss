"use client";

import { useEffect, useRef } from "react";

export type OrbState = "esperando_palabra_clave" | "escuchando" | "procesando" | "hablando" | "pausado";

/** Esfera de partículas animada en `<canvas>` (2D, sin dependencias nuevas)
 * -- inspirada en la estética de un orbe de IA tipo "asistente que escucha
 * siempre" (diseño propio, no una reproducción literal de ninguna imagen de
 * referencia). `nivelAudio` (0-1) hace que el radio/velocidad reaccionen en
 * vivo al volumen del micrófono (mientras escucha) o del audio de TTS
 * (mientras habla); sin eso, el orbe igual respira/pulsa lentamente según
 * `estado` para no quedar estático mientras "espera".
 */
export function ListeningOrb({
  estado,
  nivelAudio = 0,
  size = 220,
}: {
  estado: OrbState;
  nivelAudio?: number;
  size?: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const estadoRef = useRef(estado);
  const nivelRef = useRef(nivelAudio);
  const particulasRef = useRef<
    { theta: number; phi: number; radio: number; fase: number; velocidad: number }[]
  >([]);

  estadoRef.current = estado;
  nivelRef.current = nivelAudio;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const contexto2d = canvas.getContext("2d");
    if (!contexto2d) return;
    const ctx: CanvasRenderingContext2D = contexto2d;

    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    const N = 140;
    if (particulasRef.current.length === 0) {
      particulasRef.current = Array.from({ length: N }, () => ({
        theta: Math.random() * Math.PI * 2,
        phi: Math.acos(2 * Math.random() - 1),
        radio: 0.72 + Math.random() * 0.28,
        fase: Math.random() * Math.PI * 2,
        velocidad: 0.4 + Math.random() * 0.6,
      }));
    }

    let rotacion = 0;
    let anim = 0;
    const centro = size / 2;

    function colorPara(estado: OrbState): { desde: string; hasta: string } {
      switch (estado) {
        case "hablando":
          return { desde: "#fbbf24", hasta: "#f59e0b" }; // ámbar: Edecán está hablando
        case "escuchando":
          return { desde: "#818cf8", hasta: "#4f46e5" }; // índigo brillante: te está escuchando
        case "procesando":
          return { desde: "#a78bfa", hasta: "#7c3aed" }; // violeta: pensando
        case "pausado":
          return { desde: "#94a3b8", hasta: "#64748b" }; // gris: esperando tu confirmación
        default:
          return { desde: "#6366f1", hasta: "#4338ca" }; // índigo tenue: esperando la palabra clave
      }
    }

    function dibujar() {
      const est = estadoRef.current;
      const nivel = Math.max(0, Math.min(1, nivelRef.current));
      anim += 0.016;
      const respiracion = 0.5 + 0.5 * Math.sin(anim * (est === "procesando" ? 3.2 : 1.1));
      const energia =
        est === "escuchando" || est === "hablando"
          ? 0.35 + nivel * 0.65
          : est === "procesando"
            ? 0.55 + respiracion * 0.25
            : 0.22 + respiracion * 0.1;

      rotacion += est === "procesando" ? 0.02 : 0.004 + energia * 0.006;

      ctx.clearRect(0, 0, size, size);

      const radioBase = size * 0.34 * (0.85 + energia * 0.3);
      const { desde, hasta } = colorPara(est);

      const nucleo = ctx.createRadialGradient(centro, centro, 0, centro, centro, radioBase * 0.6);
      nucleo.addColorStop(0, `${desde}55`);
      nucleo.addColorStop(1, "transparent");
      ctx.fillStyle = nucleo;
      ctx.beginPath();
      ctx.arc(centro, centro, radioBase * 0.6, 0, Math.PI * 2);
      ctx.fill();

      for (const p of particulasRef.current) {
        const theta = p.theta + rotacion * p.velocidad;
        const x3 = Math.sin(p.phi) * Math.cos(theta);
        const y3 = Math.sin(p.phi) * Math.sin(theta);
        const z3 = Math.cos(p.phi);

        const escala = radioBase * (p.radio * (0.85 + energia * 0.35));
        const x = centro + x3 * escala;
        const y = centro + y3 * escala * 0.92;
        const profundidad = (z3 + 1) / 2; // 0 (atrás) .. 1 (adelante)

        const brillo = 0.25 + profundidad * 0.75;
        const punto = 1 + profundidad * 2.2 * (0.7 + energia * 0.6);
        const parpadeo = 0.7 + 0.3 * Math.sin(anim * 2 + p.fase);

        ctx.beginPath();
        ctx.arc(x, y, Math.max(0.4, punto), 0, Math.PI * 2);
        ctx.fillStyle = profundidad > 0.5 ? desde : hasta;
        ctx.globalAlpha = brillo * parpadeo;
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      requestAnimationFrame(dibujar);
    }

    const id = requestAnimationFrame(dibujar);
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width: size, height: size }}
      className="drop-shadow-[0_0_40px_rgba(99,102,241,0.35)]"
      aria-hidden="true"
    />
  );
}
