import pytest
from fastapi import HTTPException

import main
import schemas


def test_erp_url_requires_https_and_allowlist(monkeypatch):
    monkeypatch.setattr(main.settings, "erp_allowed_hosts", "odoo.internal.example")
    with pytest.raises(HTTPException, match="HTTPS"):
        main.validate_erp_url("http://odoo.internal.example")
    with pytest.raises(HTTPException, match="not allowed"):
        main.validate_erp_url("https://unapproved.example")


def test_instance_response_cannot_include_a_password():
    assert "password" not in schemas.InstanceOut.model_fields
    assert "password_encrypted" not in schemas.InstanceOut.model_fields


def test_llm_settings_response_cannot_include_the_api_key():
    assert "openrouter_api_key" not in schemas.LLMSettingsOut.model_fields
