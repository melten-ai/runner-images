"""Refresh the official Azure CLI's dependency closure without changing its version."""

import importlib.metadata
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse


def locked_requirements(report):
    lines = []
    names = set()
    for item in report["install"]:
        name, version = item["metadata"]["name"], item["metadata"]["version"]
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        digest = item["download_info"]["archive_info"]["hashes"]["sha256"]
        url = urlparse(item["download_info"]["url"])
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", version)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or url.scheme != "https"
            or url.netloc != "files.pythonhosted.org"
            or not url.path.endswith(".whl")
            or canonical in names
        ):
            raise ValueError("Expected unique official PyPI wheels with SHA256")
        names.add(canonical)
        lines.append(f"{name}=={version} --hash=sha256:{digest}")
    if "azure-cli" not in names:
        raise ValueError("Resolved dependencies omit Azure CLI")
    return "\n".join(lines) + "\n"


def probe(python, site, version, config):
    env = {
        **os.environ,
        "PYTHONPATH": str(site),
        "PYTHONDONTWRITEBYTECODE": "1",
        "AZURE_CONFIG_DIR": str(config),
        "AZURE_CORE_COLLECT_TELEMETRY": "false",
    }
    subprocess.run([str(python), "-s", "-m", "pip", "check"], env=env, check=True)
    output = subprocess.check_output(
        [str(python), "-s", "-m", "azure.cli", "version"], env=env, text=True
    )
    if json.loads(output)["azure-cli"] != version:
        raise ValueError("Azure CLI version changed during dependency refresh")
    clouds = subprocess.check_output(
        [str(python), "-s", "-m", "azure.cli", "cloud", "list", "-o", "json"],
        env=env,
        text=True,
    )
    if not any(c["name"] == "AzureCloud" for c in json.loads(clouds)):
        raise ValueError("Azure public cloud metadata missing")
    for group in ("vm", "storage", "account"):
        subprocess.run(
            [str(python), "-s", "-m", "azure.cli", group, "--help"],
            env=env,
            stdout=subprocess.DEVNULL,
            check=True,
        )
    subprocess.run(
        [
            str(python),
            "-s",
            "-c",
            "from cryptography.hazmat.primitives.asymmetric.ed25519 "
            "import Ed25519PrivateKey; "
            "from OpenSSL import SSL; "
            "from oauthlib.oauth2 import WebApplicationClient; "
            "import urllib3; key=Ed25519PrivateKey.generate(); "
            "key.public_key().verify(key.sign(b'runner-image'), b'runner-image'); "
            "SSL.Context(SSL.TLS_CLIENT_METHOD); "
            "assert 'client_id=example-client' in "
            "WebApplicationClient('example-client').prepare_request_uri("
            "'https://example.com/authorize'); "
            "assert urllib3.PoolManager().request('GET', "
            "'https://management.azure.com/metadata/endpoints?api-version=2020-06-01', "
            "timeout=30).status == 200",
        ],
        env=env,
        check=True,
    )


def activate(site, replacement, backup, verify):
    os.replace(site, backup)
    try:
        os.replace(replacement, site)
        verify()
    except BaseException:
        if site.exists():
            os.replace(site, replacement)
        os.replace(backup, site)
        raise


def refresh(cask, receipt):
    sites = list(cask.glob("*/libexec/lib/python*/site-packages"))
    if len(sites) != 1:
        raise ValueError("Expected one installed Azure CLI cask Python tree")
    site = sites[0]
    if any(p.is_symlink() for p in (site, *site.parents)):
        raise ValueError("Refusing a linked Azure CLI package tree")
    minor = site.parent.name.removeprefix("python")
    if not re.fullmatch(r"3\.[0-9]+", minor):
        raise ValueError("Invalid Azure CLI Python minor")
    python = Path("/opt/homebrew/opt") / f"python@{minor}" / "bin" / f"python{minor}"
    distributions = list(importlib.metadata.distributions(path=[str(site)]))
    versions = [d.version for d in distributions if d.metadata["Name"] == "azure-cli"]
    if len(versions) != 1 or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", versions[0]):
        raise ValueError("Expected installed official stable Azure CLI metadata")
    version = versions[0]
    with tempfile.TemporaryDirectory(
        prefix=".runner-image-azure-", dir=site.parent
    ) as d:
        work = Path(d)
        pip = [
            str(python),
            "-m",
            "pip",
            "--isolated",
            "install",
            "--index-url",
            "https://pypi.org/simple",
            "--only-binary=:all:",
        ]
        env = {
            k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "PYTHON"))
        }
        env["PIP_CONFIG_FILE"] = os.devnull
        subprocess.run(
            [
                *pip,
                "--dry-run",
                "--ignore-installed",
                "--report",
                str(work / "resolved.json"),
                f"azure-cli=={version}",
            ],
            env=env,
            check=True,
        )
        report = json.loads((work / "resolved.json").read_text())
        requirements = work / "requirements.txt"
        requirements.write_text(locked_requirements(report))
        replacement = work / "site-packages"
        subprocess.run(
            [
                *pip,
                "--no-deps",
                "--require-hashes",
                "--target",
                str(replacement),
                "-r",
                str(requirements),
            ],
            env=env,
            check=True,
        )
        probe(python, replacement, version, work / "azure-config")
        activate(
            site,
            replacement,
            work / "previous",
            lambda: probe(python, site, version, work / "azure-config"),
        )
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Refreshed Azure CLI {version} dependencies with verified PyPI wheels")


if __name__ == "__main__":
    refresh(
        Path("/opt/homebrew/Caskroom/azure-cli"),
        Path.home() / ".runner-image/azure-dependencies.json",
    )
