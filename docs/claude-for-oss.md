# Claude for Open Source: borrador y elegibilidad

> Estado verificado el 2026-07-20. El mantenedor informó que ya envió una
> candidatura con `isaccmanuel`; no presentar otra desde una segunda cuenta.
> Los
> [términos oficiales](https://www.anthropic.com/claude-for-oss-terms)
> pueden cambiar y deben revisarse de nuevo antes de aplicar mediante el
> [formulario oficial](https://claude.com/contact-sales/claude-for-oss).

## Borrador de candidatura

El texto siguiente tiene 170 palabras: está holgadamente por debajo del
máximo de 500 palabras vigente en la fecha de revisión. Recontarlo y
reconfirmar ese límite antes de enviarlo.

Mantengo [Edecan](https://github.com/bizagencysas/edecan-oss), un asistente
personal de IA distribuido bajo Apache-2.0. El proyecto reúne un backend
Python/FastAPI, una aplicación web Next.js, un shell de escritorio
Tauri/Rust y clientes nativos Swift y Kotlin. Su diseño prioriza
self-hosting, credenciales bring-your-own, aislamiento por tenant,
confirmación humana para acciones sensibles y pruebas offline
deterministas. La verificación completa del 2026-07-20 terminó con 4,190
tests aprobados y 34 omitidos por depender de infraestructura opcional.

Usaría Claude Max para mejorar el proyecto público: revisar contratos entre
plataformas, ampliar pruebas de regresión y end-to-end, endurecer límites de
seguridad, reducir deuda técnica, preparar releases reproducibles y hacer
que la documentación y los flujos de contribución sean más accesibles.
Claude también ayudaría a revisar cambios grandes sin sacrificar
compatibilidad entre API, web, escritorio, iOS y Android.

Edecan es un proyecto joven. No afirmo todavía dependientes, descargas,
adopción externa ni impacto de ecosistema que la evidencia pública no
demuestra. Mi objetivo es construir esa utilidad de forma abierta y
medible, publicar releases verificables y desarrollar una comunidad real
de usuarios y contribuidores.

## Checklist honesto

- [x] **Proyecto público con licencia OSI:** Apache-2.0, confirmado por
  `LICENSE`, `pyproject.toml` y GitHub.
- [x] **Actividad OSS pública reciente:** el repositorio tuvo actividad
  pública dentro de los 90 días anteriores a esta revisión. Debe
  reconfirmarse al aplicar.
- [ ] **Cuenta GitHub de al menos dos años — bloqueador:** `isaccmanuel` fue
  creada el 2026-07-10. No cumple el requisito general; la fecha más
  temprana posible sería 2028-07-10, si el programa y sus términos siguen
  vigentes.
- [ ] **La segunda cuenta tampoco cumple todavía:** `bizagencysas` fue creada
  el 2025-04-08. Su fecha más temprana posible sería 2027-04-08, bajo los
  términos vigentes en esta revisión.
- [ ] **No duplicar la candidatura:** los términos indican que las solicitudes
  concurrentes o duplicadas de una misma persona, incluso con otra cuenta o
  correo, pueden descartarse. También prohíben usar varias identidades para
  obtener más de una suscripción. Publicar un espejo del código es válido;
  usarlo para repetir la candidatura no lo es.
- [ ] **Maintainer Track:** no demostrado. La evidencia pública actual no
  acredita los umbrales de dependientes, descargas, PR externos, 20
  contribuidores externos, fundación reconocida u OpenSSF criticality.
- [ ] **Ecosystem Impact Track:** no demostrado todavía; no hay evidencia
  pública de que el ecosistema dependa materialmente del proyecto.
- [ ] **Requisitos personales:** edad, residencia, sanciones, relación con
  Anthropic y condición de persona natural deben ser confirmados por quien
  aplica; no pueden inferirse del repositorio.

No deben fabricarse métricas ni actividad para cumplir umbrales. Anthropic
indica que puede descartar actividad trivial, automatizada o inflada.
