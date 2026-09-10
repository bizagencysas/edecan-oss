from __future__ import annotations

import uuid

from edecan_core.bot_routing import select_responder_for_team


def _worker(
    *,
    name: str,
    purpose: str,
    job_description: str = "",
    role_title: str = "",
) -> dict[str, str]:
    return {
        "id": str(uuid.uuid4()),
        "name": name.lower().replace(" ", "-"),
        "display_name": name,
        "purpose": purpose,
        "job_description": job_description,
        "role_title": role_title,
    }


TEAM = [
    _worker(
        name="Frontend",
        purpose="Arreglar UI, login, SwiftUI y pantallas iOS.",
        job_description="Especialista frontend mobile",
        role_title="Frontend",
    ),
    _worker(
        name="Backend",
        purpose="API FastAPI, smoke tests, endpoints y servidor.",
        job_description="Ingeniero backend",
        role_title="Backend",
    ),
    _worker(
        name="Community",
        purpose="Community manager: borradores LinkedIn y contenido social.",
        job_description="Redes y posts",
        role_title="Community manager",
    ),
    _worker(
        name="Bugs",
        purpose="Reproducir bugs y dejar tickets con evidencia.",
        job_description="QA y reproducción de fallos",
        role_title="Bugs",
    ),
]


def test_arregla_login_ui_asigna_frontend():
    result = select_responder_for_team(TEAM, "arregla el login de la UI")
    assert result.display_name == "Frontend"
    assert result.archetype == "frontend_ui"
    assert result.agent_id


def test_smokea_api_asigna_backend():
    result = select_responder_for_team(TEAM, "smokea la API del worker")
    assert result.display_name == "Backend"
    assert result.archetype == "backend_api"


def test_borrador_linkedin_asigna_community():
    result = select_responder_for_team(TEAM, "borrador de post para LinkedIn")
    assert result.display_name == "Community"
    assert result.archetype == "community_social"


def test_reproduce_bug_asigna_bugs():
    result = select_responder_for_team(TEAM, "reproduce este bug en producción")
    assert result.display_name == "Bugs"
    assert result.archetype == "bugs_qa"
