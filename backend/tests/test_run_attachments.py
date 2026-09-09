import pytest
from pydantic import ValidationError

import schemas


def test_run_attachments_are_validated_and_preserved():
    payload = schemas.RunCreate(message="Review these", attachments=[{
        "name": "notes.txt", "type": "text/plain", "content": "data:text/plain;base64,SGk=", "text": "Hi",
    }])
    assert payload.attachments[0].name == "notes.txt"
    assert payload.attachments[0].text == "Hi"


def test_run_attachment_rejects_non_data_content():
    with pytest.raises(ValidationError):
        schemas.RunCreate(message="Review", attachments=[{"name": "x", "type": "text/plain", "content": "raw"}])
