import base64
import sys
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).parents[1]))
import deployment
import models
from config import settings


def test_execute_bridge_creates_signed_job():
    """
    Regression test to ensure execute_bridge correctly builds and signs a job
    without crashing (e.g. from new_token argument errors).
    """
    private = Ed25519PrivateKey.generate()
    config = {
        "signing_private_key": base64.b64encode(private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption()
        )).decode(),
        "bridge_url": "http://localhost",
        "bridge_token": "token",
    }
    
    dep = models.Deployment(id="deployment-1", external_job_id=None, status="pending")
    inst = models.Instance(id=1)
    art = models.Artifact(name="test_mod", version="19.0.1.0.0", digest="0"*64)

    with patch("deployment.rpc") as mock_rpc:
        deployment.execute_bridge(dep, inst, art, config, db=None)
        
    assert dep.external_job_id == "deployment-1"
    assert dep.status == "deploying"
    assert mock_rpc.called
