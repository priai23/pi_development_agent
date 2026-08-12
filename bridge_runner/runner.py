import os
import json
import time
import requests
import base64
import hashlib
import zipfile
import subprocess
from urllib.parse import urlparse
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

CONFIG_PATH = os.environ.get('PRIMACY_RUNNER_CONFIG', 'config.json')
BRIDGE_TOKEN = os.environ.get('PRIMACY_BRIDGE_TOKEN')

def load_config():
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

from pathlib import Path

class RunnerError(Exception):
    pass

def canonical_job(job: dict) -> bytes:
    clean = {k: v for k, v in job.items() if k != "signature"}
    return json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")

def verify_job(job: dict, public_key: Ed25519PublicKey, seen_nonces: set) -> None:
    if job.get("nonce") in seen_nonces:
        raise RunnerError("Job nonce already used")
    if "signature" not in job:
        raise RunnerError("Missing signature")
    signature = base64.b64decode(job["signature"])
    try:
        public_key.verify(signature, canonical_job(job))
    except InvalidSignature:
        raise RunnerError("Invalid signature")
    seen_nonces.add(job["nonce"])

def verify_digest(payload_bytes: bytes, expected_digest: str) -> bool:
    actual_digest = hashlib.sha256(payload_bytes).hexdigest()
    return actual_digest == expected_digest

def download_artifact(url: str, allowed_hosts: list) -> bytes:
    parsed_url = urlparse(url)
    if parsed_url.hostname not in allowed_hosts:
        raise ValueError(f"Artifact URL host {parsed_url.hostname} is not allowed")
    
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content

def safe_extract(zip_path: str, extract_to: str, expected_module_name: str) -> Path:
    extract_to_path = Path(extract_to).resolve()
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.namelist():
            target_path = (extract_to_path / member).resolve()
            if not str(target_path).startswith(str(extract_to_path)):
                raise RunnerError(f"unsafe path traversal in ZIP: {member}")
            
            # Reject symlinks if present to prevent symlink attacks
            if getattr(zf.getinfo(member), 'external_attr', 0) >> 16 & 0o120000 == 0o120000:
                raise RunnerError(f"Symlinks are not allowed in deployment packages: {member}")
                
        zf.extractall(extract_to_path)
        
    return extract_to_path / expected_module_name

def restart_odoo(command: list):
    subprocess.run(command, check=True)

def update_job_status(bridge_url: str, job_id: int, status: str, logs: str = ""):
    url = f"{bridge_url.rstrip('/')}/primacy_bridge/jobs/{job_id}"
    headers = {"Authorization": f"Bearer {BRIDGE_TOKEN}"}
    payload = {"status": status, "logs": logs}
    try:
        requests.put(url, json=payload, headers=headers, timeout=10)
    except Exception as e:
        print(f"Failed to update job status: {e}")

def process_job(job: dict, config: dict):
    job_id = job['id']
    try:
        update_job_status(config['bridge_url'], job_id, "running", "Started processing deployment job")
        
        # 1. Download artifact
        print(f"Downloading {job['artifact_url']}...")
        artifact_bytes = download_artifact(job['artifact_url'], config['allowed_artifact_hosts'])
        
        # 2. Verify signature
        # We assume the signature was generated over the artifact bytes
        if not verify_signature(config['public_key_base64'], job['signature_base64'], artifact_bytes):
            raise ValueError("Ed25519 signature verification failed")
            
        # 3. Verify digest
        if not verify_digest(artifact_bytes, job['digest']):
            raise ValueError("SHA-256 digest verification failed")
            
        # 4. Save and extract securely
        temp_zip = f"/tmp/primacy_deploy_{job_id}.zip"
        with open(temp_zip, "wb") as f:
            f.write(artifact_bytes)
            
        print("Extracting module...")
        safe_extract(temp_zip, config['custom_addons_path'])
        
        # 5. Restart Odoo
        print("Restarting Odoo...")
        restart_odoo(config['restart_command'])
        
        update_job_status(config['bridge_url'], job_id, "success", "Deployment completed successfully")
        print(f"Job {job_id} succeeded")
        
    except Exception as e:
        update_job_status(config['bridge_url'], job_id, "failed", f"Error: {str(e)}")
        print(f"Job {job_id} failed: {e}")

def main():
    if not BRIDGE_TOKEN:
        print("Error: PRIMACY_BRIDGE_TOKEN environment variable is missing")
        return
        
    config = load_config()
    print(f"Starting bridge runner. Polling {config['bridge_url']}...")
    
    while True:
        try:
            url = f"{config['bridge_url'].rstrip('/')}/primacy_bridge/jobs?status=pending"
            headers = {"Authorization": f"Bearer {BRIDGE_TOKEN}"}
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                jobs = response.json()
                for job in jobs:
                    process_job(job, config)
            else:
                print(f"Failed to fetch jobs. Status: {response.status_code}")
                
        except requests.exceptions.RequestException as e:
            print(f"Connection error: {e}")
            
        time.sleep(10)

if __name__ == "__main__":
    main()
