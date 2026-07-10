from __future__ import annotations

from edecan_schemas.tokens import TokenBundle


def test_token_bundle_defaults():
    bundle = TokenBundle(access_token="abc123")
    assert bundle.access_token == "abc123"
    assert bundle.refresh_token is None
    assert bundle.expires_at is None
    assert bundle.scopes == []
    assert bundle.token_type == "bearer"


def test_token_bundle_roundtrip_json():
    bundle = TokenBundle(
        access_token="abc123",
        refresh_token="refresh456",
        scopes=["email", "calendar"],
    )
    restored = TokenBundle.model_validate_json(bundle.model_dump_json())
    assert restored == bundle
