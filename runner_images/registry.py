"""Upload a qualified candidate and verify its immutable GHCR identity."""

import base64
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from runner_images.qualification import digest, fingerprint, read_object
from runner_images.rebuild import input_id


def fetch(url: str, headers: dict[str, str]) -> bytes:
    with urlopen(Request(url, headers=headers), timeout=90) as response:
        return response.read()


def manifest(reference: str, qualification_hash: str, inputs: str) -> str:
    match = re.fullmatch(
        r"ghcr.io/(melten-ai/runner-images/macos-26-(?:xcode|xcode-android))"
        r"(?::(macos-26-[a-z-]+-(?:[0-9]+-[0-9]+|local-[a-f0-9]{32}))"
        r"|@(sha256:[a-f0-9]{64}))",
        reference,
    )
    if not match:
        raise ValueError("unexpected registry destination")
    package, tag, expected = match.groups()
    headers: dict[str, str] = {}
    password = os.environ.get("TART_REGISTRY_PASSWORD") or os.environ.get("GH_TOKEN")
    if password:
        credentials = f"{os.environ.get('TART_REGISTRY_USERNAME', 'token')}:{password}"
        headers["Authorization"] = (
            "Basic " + base64.b64encode(credentials.encode()).decode()
        )
    token = json.loads(
        fetch(
            "https://ghcr.io/token?"
            + urlencode(
                {
                    "service": "ghcr.io",
                    "scope": f"repository:{package}:pull",
                }
            ),
            headers,
        )
    )["token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.oci.image.manifest.v1+json",
    }
    raw = fetch(f"https://ghcr.io/v2/{package}/manifests/{expected or tag}", headers)
    identity = "sha256:" + hashlib.sha256(raw).hexdigest()
    if expected and identity != expected:
        raise ValueError("registry manifest digest changed")
    descriptor = json.loads(raw)["config"]["digest"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", descriptor):
        raise ValueError("invalid OCI config digest")
    config = fetch(f"https://ghcr.io/v2/{package}/blobs/{descriptor}", headers)
    if "sha256:" + hashlib.sha256(config).hexdigest() != descriptor:
        raise ValueError("registry config digest changed")
    labels = json.loads(config)["config"]["Labels"]
    if (
        labels.get("ai.melten.qualification") != qualification_hash
        or labels.get("ai.melten.inputs") != inputs
    ):
        raise ValueError("uploaded candidate does not match qualified inputs")
    return f"ghcr.io/{package}@{identity}"


def publish(bundle: Path, root: Path, image_directory: Path) -> None:
    receipt = read_object(bundle / "qualification.json")
    build = cast(dict[str, str], receipt["build"])
    if receipt.get("status") != "passed" or receipt.get("image_files") != fingerprint(
        image_directory
    ):
        raise ValueError("candidate bytes changed before upload")
    inputs = input_id(root, build["profile"])
    qualification_hash = digest(bundle / "qualification.json")
    remote = (
        f"ghcr.io/melten-ai/runner-images/macos-26-{build['profile']}:{build['image']}"
    )
    subprocess.run(
        [
            "tart",
            "push",
            build["image"],
            remote,
            "--chunk-size",
            "3",
            "--label",
            f"ai.melten.qualification={qualification_hash}",
            "--label",
            f"ai.melten.inputs={inputs}",
            "--label",
            "org.opencontainers.image.source=https://github.com/melten-ai/runner-images",
            "--label",
            f"org.opencontainers.image.revision={build['source_commit']}",
        ],
        check=True,
        timeout=14400,
    )
    if receipt["image_files"] != fingerprint(image_directory):
        raise ValueError("candidate bytes changed during upload")
    reference = manifest(remote, qualification_hash, inputs)
    (bundle / "publication.json").write_text(
        json.dumps(
            {
                "status": "uploaded",
                "input_id": inputs,
                "reference": reference,
                "qualification_sha256": qualification_hash,
            },
            indent=2,
        )
        + "\n"
    )


def record_publication(bundle: Path, root: Path, destination: Path) -> None:
    receipt = read_object(bundle / "publication.json")
    build = cast(dict[str, str], read_object(bundle / "qualification.json")["build"])
    inputs = input_id(root, build["profile"])
    qualification_hash = digest(bundle / "qualification.json")
    reference = receipt.get("reference")
    if (
        receipt.get("status") != "uploaded"
        or receipt.get("input_id") != inputs
        or receipt.get("qualification_sha256") != qualification_hash
        or not isinstance(reference, str)
        or "@sha256:" not in reference
        or not reference.startswith(
            f"ghcr.io/melten-ai/runner-images/macos-26-{build['profile']}@"
        )
    ):
        raise ValueError("invalid publication receipt")
    manifest(reference, qualification_hash, inputs)  # independent hosted read-back
    target = destination / "publication.json"
    content = (bundle / "publication.json").read_bytes()
    if target.exists() and target.read_bytes() != content:
        raise ValueError("publication record is immutable")
    target.write_bytes(content)
    summary = destination / "README.md"
    local_status = (
        "This is a local image on the builder; registry publication is separate."
    )
    summary.write_text(
        summary.read_text().replace(
            local_status,
            f"Uploaded candidate: `{reference}`. "
            "[Publication receipt](publication.json) records the registry read-back.",
        )
    )
