# Política de seguridad

Edecan está en fase **pre-1.0**. El proyecto no afirma contar con SOC 2,
ISO 27001, una auditoría externa ni una certificación formal. El diseño, los
límites de confianza y los riesgos conocidos se documentan en el
[modelo de amenazas](./docs/seguridad-modelo-amenazas.md).

## Reportar una vulnerabilidad

No abras un issue público. Usa el formulario privado de
[GitHub Security Advisories](https://github.com/bizagencysas/edecan-oss/security/advisories/new).
Si GitHub no te permite enviar el reporte, abre un issue que solo diga que
necesitas un canal privado, sin incluir detalles técnicos ni datos sensibles.

Incluye, cuando sea posible:

- versión, commit y superficie afectada;
- impacto y condiciones necesarias para explotarla;
- reproducción mínima y determinista;
- mitigación temporal conocida;
- si autorizas crédito público tras la corrección.

El equipo intentará confirmar la recepción en cinco días hábiles, compartir
una evaluación inicial y coordinar la divulgación después de que exista una
corrección o mitigación. Los tiempos dependen de la severidad y complejidad;
este plazo es un objetivo de respuesta, no una garantía contractual.

## Alcance

Está dentro de alcance todo el contenido versionado en este repositorio:
`apps/`, `packages/`, clientes nativos, scripts, configuración de despliegue
y cadena de build. Extensiones privadas o distribuidas por separado tienen su
propio proceso y no están cubiertas por esta política.

Áreas de especial sensibilidad:

- aislamiento multi-tenant y cualquier omisión de Row-Level Security;
- TokenVault, claves de cifrado y filtración de credenciales en logs/errores;
- autenticación, pairing, OAuth state y sesiones WebSocket/SSE;
- bypass de aprobación en tools peligrosas o control remoto;
- SSRF, traversal o escape de sandbox en browser, MCP, archivos y companion;
- supply chain de skills, dependencias, sidecars e instaladores;
- consentimiento y opt-out en cualquier integración de mensajería.

Quedan fuera de alcance la ingeniería social, la denegación de servicio
volumétrica, el acceso a datos de terceros y pruebas destructivas contra una
instancia que no te pertenece. Los errores derivados únicamente de ignorar una
advertencia explícita y configurar una instancia de forma deliberadamente
insegura pueden tratarse como hardening, aunque agradecemos el reporte.

## Investigación de buena fe

Consideramos de buena fe la investigación que evita degradar servicios, usa
solo cuentas/datos propios, accede al mínimo necesario para demostrar el
problema y se reporta de forma privada antes de divulgar detalles explotables.
Esto no autoriza acceso masivo, persistencia, extorsión, exfiltración de datos
ni acciones fuera de la ley aplicable.

## Versiones soportadas

Mientras el proyecto sea pre-1.0, las correcciones de seguridad se publican
para la rama `main`. Los tags anteriores pueden quedar vulnerables; actualiza
al commit o release que el advisory indique.

## Operación segura

- No uses los placeholders de `.env.example` en producción. La aplicación
  rechaza los secretos públicos, pero el operador sigue siendo responsable de
  rotación, backups y control de acceso.
- Los tests deben ser offline y usar fakes; nunca llames servicios pagados o
  cuentas reales desde CI.
- No incluyas secretos ni datos personales reales en commits, fixtures,
  capturas, logs o issues.
- Revisa los runbooks en [`docs/runbooks/`](./docs/runbooks/) antes de operar
  una instancia con datos reales.

### Sesiones web y pestañas duplicadas

El cliente web guarda los JWT en `sessionStorage`: no persisten al cerrar la
pestaña y no se copian deliberadamente a `localStorage`. Las solicitudes
concurrentes de una misma pestaña comparten una única rotación y una respuesta
tardía no puede restaurar una sesión cerrada ni sobrescribir un login nuevo.

No se transmiten refresh tokens por `BroadcastChannel` para coordinar pestañas,
porque eso ampliaría el secreto a cualquier contexto del mismo origen. Algunos
navegadores copian `sessionStorage` al **duplicar** una pestaña; como el backend
rota el refresh token de un solo uso, la primera copia que renueve la sesión
puede hacer que la otra solicite autenticarse de nuevo. Es una limitación
intencional: abre Edecan en una sola pestaña por sesión o inicia sesión de forma
independiente en cada pestaña, en vez de duplicarla.
