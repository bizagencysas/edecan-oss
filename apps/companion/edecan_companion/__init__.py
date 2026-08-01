"""edecan_companion — companion local de escritorio (opt-in) de Edecán.

CLI que el usuario instala y corre en SU máquina para darle al asistente
acceso controlado, con sandbox y siempre aprobado explícitamente, a su
equipo (ver `ARCHITECTURE.md` §10.7 "extras['companion']" y §10.12 "rutas
/v1/companion/*", y el `README.md` de este paquete para el flujo completo).

Nunca se activa solo: hace falta instalarlo y emparejarlo a mano con
`python -m edecan_companion --server ... --code ...`.
"""

from __future__ import annotations

__version__ = "0.6.0"
