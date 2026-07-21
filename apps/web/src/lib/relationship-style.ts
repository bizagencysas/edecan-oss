import type { PersonaConfig, RelationshipStyle } from "./types";

export const RELATIONSHIP_STYLE_OPTIONS: ReadonlyArray<{
  value: RelationshipStyle;
  title: string;
  description: string;
}> = [
  {
    value: "profesional",
    title: "Profesional / socio",
    description: "Claro, práctico y a tu lado para comparar opciones y decidir.",
  },
  {
    value: "coach",
    title: "Coach",
    description: "Te ayuda a avanzar y convertir objetivos en pasos, sin decidir por ti.",
  },
  {
    value: "amigo",
    title: "Amigo",
    description: "Cercano y relajado, dejando siempre claro que Edecan es una IA.",
  },
  {
    value: "romantico",
    title: "Romántico",
    description: "Más cariñoso y coqueto, sin fingir sentimientos ni una relación humana.",
  },
];

export function buildRelationshipPatch(
  estilo: RelationshipStyle,
  adultoConfirmado: boolean,
  consentimientoExplicito: boolean,
): Pick<
  PersonaConfig,
  "estilo_relacion" | "adulto_confirmado" | "consentimiento_romantico"
> {
  if (estilo === "romantico") {
    if (!adultoConfirmado || !consentimientoExplicito) {
      throw new Error(
        "Para activar el estilo romántico confirma que tienes 18 años o más y que lo eliges sabiendo que Edecan es una IA.",
      );
    }
    return {
      estilo_relacion: estilo,
      adulto_confirmado: true,
      consentimiento_romantico: true,
    };
  }
  return {
    estilo_relacion: estilo,
    adulto_confirmado: false,
    consentimiento_romantico: false,
  };
}

export const EXIT_ROMANTIC_PATCH = buildRelationshipPatch("profesional", false, false);
