import assert from "node:assert/strict";
import test from "node:test";

import {
  buildRelationshipPatch,
  EXIT_ROMANTIC_PATCH,
  RELATIONSHIP_STYLE_OPTIONS,
} from "./src/lib/relationship-style.ts";

test("expone cuatro estilos humanos y estables", () => {
  assert.deepEqual(
    RELATIONSHIP_STYLE_OPTIONS.map(({ value }) => value),
    ["profesional", "coach", "amigo", "romantico"],
  );
});

test("romántico exige mayoría de edad y consentimiento explícito", () => {
  assert.throws(() => buildRelationshipPatch("romantico", false, true), /18 años/);
  assert.throws(() => buildRelationshipPatch("romantico", true, false), /Edecan es una IA/);
  assert.deepEqual(buildRelationshipPatch("romantico", true, true), {
    estilo_relacion: "romantico",
    adulto_confirmado: true,
    consentimiento_romantico: true,
  });
});

test("cualquier estilo no romántico limpia consentimientos anteriores", () => {
  assert.deepEqual(buildRelationshipPatch("amigo", true, true), {
    estilo_relacion: "amigo",
    adulto_confirmado: false,
    consentimiento_romantico: false,
  });
  assert.deepEqual(EXIT_ROMANTIC_PATCH, {
    estilo_relacion: "profesional",
    adulto_confirmado: false,
    consentimiento_romantico: false,
  });
});
