from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from edecan_schemas.models import PersonaConfig, TenantOut, UserOut


def test_persona_config_defaults_exactos():
    p = PersonaConfig()
    assert p.nombre_asistente == "Edecán"
    assert p.idioma == "es"
    assert p.tono == "cálido y profesional"
    assert p.formalidad == 1
    assert p.emojis is False
    assert p.instrucciones == ""
    assert p.rasgos == []
    assert p.memoria_activada is True
    assert p.voice_id is None


def test_persona_config_formalidad_fuera_de_rango_falla():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PersonaConfig(formalidad=4)
    with pytest.raises(ValidationError):
        PersonaConfig(formalidad=-1)


def test_persona_config_rasgos_no_comparte_lista_mutable_entre_instancias():
    a = PersonaConfig()
    b = PersonaConfig()
    a.rasgos.append("curioso")
    assert b.rasgos == []


def test_tenant_out_roundtrip():
    tenant = TenantOut(
        id=uuid4(),
        name="Acme",
        slug="acme",
        plan_key="hosted_pro",
        status="active",
        created_at=datetime.now(UTC),
    )
    data = tenant.model_dump(mode="json")
    assert TenantOut.model_validate(data) == tenant


def test_user_out_no_expone_password_hash():
    user = UserOut(id=uuid4(), email="demo@example.com", created_at=datetime.now(UTC))
    assert "password_hash" not in user.model_dump()
    assert user.is_superadmin is False
