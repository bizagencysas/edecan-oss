# Actualizaciones sin volver a clonar

La app instalada de Edecán se actualiza desde **Ajustes → Actualizaciones** en
macOS, Windows y Linux. También busca en silencio al abrirla y al volver a
primer plano, con un intervalo máximo de cuatro horas. Si encuentra una
versión nueva muestra un aviso; un fallo de internet no interrumpe el chat ni
el backend local.

La actualización sustituye solamente los binarios de la aplicación. La base de
datos, conversaciones, memorias, credenciales cifradas, archivos y
configuración viven en la carpeta de datos del usuario y no forman parte del
paquete descargado.

> Las instalaciones anteriores a la primera versión que incluya este
> actualizador necesitan instalar esa versión una sola vez con el instalador
> normal. A partir de allí reciben las siguientes versiones desde la propia
> app.

## Canales

- **Estable**: recomendado para la mayoría. Solo recibe versiones finales.
- **Vista previa**: recibe versiones SemVer con sufijo, por ejemplo
  `0.8.0-beta.1`.

La selección se guarda localmente. Los dos canales usan la misma raíz de
confianza; cambiar de canal no permite instalar un archivo sin firma.

Los punteros públicos viven en la rama independiente `update-channels`:

```text
stable.json
preview.json
```

Cada manifiesto apunta a artefactos inmutables de un GitHub Release. El
manifiesto puede ser público porque Tauri verifica el paquete completo con la
clave pública compilada en la app antes de instalarlo.

## Modelo de seguridad

Edecán usa `tauri-plugin-updater` y firmas minisign:

1. La clave pública está en `apps/desktop/src-tauri/tauri.conf.json`.
2. La clave privada no vive en Git, en el instalador ni en el manifiesto.
3. El workflow genera un paquete y una firma por formato instalado:
   `.app`, AppImage, `.deb`, `.rpm`, NSIS y MSI.
4. `generate-update-manifest.py` falla si falta un formato, hay dos
   candidatos o una firma está vacía. El manifiesto conserva el tipo exacto
   para que, por ejemplo, Debian nunca intente instalar una AppImage ni MSI
   intente ejecutar el instalador NSIS.
5. El puntero del canal se mueve únicamente después de publicar todos los
   artefactos y el manifiesto.
6. La app rechaza cualquier artefacto cuya firma no corresponda a la clave
   pública incorporada.

La firma del updater y la firma del sistema operativo resuelven problemas
distintos. Para distribución pública también deben configurarse Developer ID
y notarización en macOS y Authenticode en Windows. Una firma Tauri válida no
elimina por sí sola las advertencias de Gatekeeper o SmartScreen.

## Publicar una versión

El workflow [`.github/workflows/release-desktop.yml`](../.github/workflows/release-desktop.yml)
se ejecuta con tags `v*`, valida que el tag y las versiones de Cargo/Tauri
coincidan, exige que el tag pertenezca a `main`, construye y prueba cada
plataforma de forma nativa y publica el release. Los tags finales mueven
`stable.json`; los prereleases mueven `preview.json`. Un canal nunca retrocede
a una versión SemVer anterior y solo un release puede mover los canales a la
vez.

Antes del primer release configura estos secrets en el repositorio:

| Secret | Contenido |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | Contenido completo de la clave privada del updater |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Contraseña de esa clave, vacía si fue creada sin contraseña |

La clave inicial se generó fuera del repositorio en:

```text
~/Library/Application Support/Edecan Release/updater.key
```

Ese archivo debe respaldarse en un gestor de secretos con control de acceso.
Perderlo impide actualizar las instalaciones existentes. Nunca se pega en un
issue, chat, log o commit. El `.pub` de la misma carpeta sí es público.

Ejemplo de release estable:

```bash
# primero actualiza las versiones del crate, tauri.conf.json y apps/web
git tag -s v0.8.0 -m "Edecán 0.8.0"
git push bizagency v0.8.0
```

Ejemplo de vista previa:

```bash
git tag -s v0.8.0-beta.1 -m "Edecán 0.8.0 beta 1"
git push bizagency v0.8.0-beta.1
```

## Recuperación y rollback

Los releases anteriores permanecen disponibles. No habilitamos downgrades
remotos silenciosos porque volver a un binario antiguo después de migrar datos
puede ser destructivo.

El rollback de producción es **hacia adelante**:

1. parte del código de la última versión sana;
2. conserva cualquier migración ya aplicada o agrega una migración
   compatible;
3. publica un parche con SemVer mayor, por ejemplo `0.8.1`;
4. el canal apunta al parche firmado nuevo.

Si el updater falla antes de instalar, la versión existente sigue intacta. En
Windows el instalador usa modo pasivo y el proceso local se apaga antes de
reemplazar archivos. En macOS y Linux Edecán apaga el sidecar antes de
reiniciar.

## Builds locales y forks OSS

`build-app.sh` y `build-app.ps1` siguen funcionando sin una clave privada:
crean el instalador normal, pero no artefactos de actualización. Si detectan
`TAURI_SIGNING_PRIVATE_KEY` o `TAURI_SIGNING_PRIVATE_KEY_PATH`, activan
`createUpdaterArtifacts` y producen los `.sig`.

Un fork debe generar su propio par de claves y cambiar tanto la clave pública
como los endpoints. Reutilizar el identificador o la clave oficial haría que
el fork dependiera del canal de otra organización.

iOS usa la actualización firmada de App Store. Android puede usar Google Play
o el canal firmado de la distribución OSS, descrito en
[`movil-android.md`](./movil-android.md). El updater Tauri de esta página se
limita deliberadamente a macOS, Windows y Linux.
