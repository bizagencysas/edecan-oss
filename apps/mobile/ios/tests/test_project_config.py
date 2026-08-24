from pathlib import Path

PROJECT_YML = Path(__file__).resolve().parents[1] / "project.yml"
INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install_device.sh"


def test_visible_name_keeps_accent_but_executable_name_is_codesign_safe() -> None:
    project = PROJECT_YML.read_text(encoding="utf-8")

    assert "CFBundleDisplayName: Edecán" in project
    assert "PRODUCT_NAME: Edecan" in project
    assert "PRODUCT_NAME: Edecán" not in project


def test_camera_permission_explains_qr_and_chat_photo_usage() -> None:
    """La cámara ya no es "solo QR": desde "iOS photo vision" (330de23) también
    se usa para adjuntar fotos en el chat -- el texto real explica AMBOS usos,
    así que la aserción anterior ("únicamente para escanear el QR") describía
    un producto que ya no existe. Se fija contra el texto vigente en vez de
    reescribir `project.yml` para que encaje con una afirmación vieja."""
    project = PROJECT_YML.read_text(encoding="utf-8")

    assert "NSCameraUsageDescription:" in project
    assert "escanear el QR de conexión" in project
    assert "adjuntar fotos" in project


def test_instalador_fisico_exige_identidad_del_operador() -> None:
    script = INSTALLER.read_text(encoding="utf-8")

    assert 'BUNDLE_ID="${EDECAN_IOS_BUNDLE_ID:-cc.edecan.app}"' in script
    assert 'DEVICE_ID="${EDECAN_IOS_DEVICE_ID:-}"' in script
    assert 'TEAM_ID="${DEVELOPMENT_TEAM:-}"' in script
    assert 'APP_GROUP="${EDECAN_IOS_APP_GROUP:-group.cc.edecan.app}"' in script
    assert 'share_path="$app_path/PlugIns/EdecanShare.appex"' in script
    assert "group.cc.edecan.app" in script
    assert "EDECAN_ALLOW_PROFILE_FETCH:-0" in script
    assert "no hay un provisioning profile local" in script
