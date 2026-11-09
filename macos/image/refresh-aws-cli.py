"""Backport supplier urllib3 security fixes within AWS CLI's supported range."""

import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def source_inputs():
    return {
        "source": {
            "url": "https://files.pythonhosted.org/packages/c7/24/5f1b3bdffd70275f6661c76461e25f024d5a38a46f04aaca912426a2b1d3/urllib3-2.6.3.tar.gz",
            "sha256": (
                "1b62b6884944a57dbe321509ab94fd4d3b307075e0c2eae991ac71ee15ad38ed"
            ),
        },
        "patches": [
            {
                "commit": "2bdcc44d1e163fb5cc48a8662425e35e15adfe6a",
                "patch_sha256": (
                    "e14222b8d838858cb75a339a59cca85ddb1e25d519dc66b91b13496215521938"
                ),
            },
            {
                "commit": "5ec0de499b9166ca71c65ab04f2a7e4eb0d66fcc",
                "patch_sha256": (
                    "c108a480f2dfd01b42ceb204da101ff9f22cf3eb563a60f2f5b1a2f5dab8491c"
                ),
            },
            {
                "commit": "ae1f7da020e9626783c6b9920719f057ce1d0435",
                "patch_sha256": (
                    "df4c60ce67cd6d375467f70cd3a42a4b2e695f24c0cb89fe77e466a0aad4a090"
                ),
            },
            {
                "commit": "cd770b059b543be29298ea5c52afb0b1b090f5ed",
                "patch_sha256": (
                    "fc64a3d5318515b1b7b4210da69b6e54fd4a7b985ea4496638f0b666d83fd2cd"
                ),
            },
            {
                "commit": "b6447295fff7b38fdffc67e0df9712d60cef3cc3",
                "patch_sha256": (
                    "8c6b6e355b50d5dcae2e869724dc382083d903b9ff65c85f64ff58d4f56990b4"
                ),
            },
            {
                "commit": "07408cec79d1856d81bb42c74a904a24fdb9e465",
                "patch_sha256": (
                    "65ced8ded0dc423b7b34442b94f6371e98228ad66a6f1d52cf080dfe5aa6bd42"
                ),
            },
            {
                "commit": "627636551e0e0159996b0f28dd21a60372cc5b10",
                "patch_sha256": (
                    "6e5f13b1b60313fbd8a70d0aab58f7c6a413f641ad6fa2e5a3539e3de68bbe27"
                ),
            },
        ],
        "modules": {
            "poolmanager.py": (
                "5fd587ec85eb6fa1e49c2eab36951748866cea20a3e64ef609845229ed712958"
            ),
            "response.py": (
                "f2d828f0f4364a739db23d76f9b1c66fe3de4a9807c5efa0ce515dd93d34b4fd"
            ),
            "connection.py": (
                "1137a6ddacd7bc5627b6dd1fa2d98dc060284c908cce0e68e27303049145e00f"
            ),
            "connectionpool.py": (
                "f9f798e58758887e008adc38869763d621271f25acc96473fd9ae5f16d021165"
            ),
            "util/ssl_.py": (
                "63d44d9160887a10f1211bf21671d48e23253e8966dfaf0660cc9ad9874339a8"
            ),
        },
    }


def build_version(plan):
    digest = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return "2.6.3+melten." + digest[:12]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url, digest, target):
    subprocess.run(
        ["curl", "-fsSL", "--retry", "5", url, "-o", str(target)], check=True
    )
    if sha256(target) != digest:
        raise ValueError("AWS urllib3 source/patch checksum mismatch")


def verify_modules(directory, expected):
    for name, digest in expected.items():
        path = directory / name
        if path.is_symlink() or sha256(path) != digest:
            raise ValueError(
                "AWS urllib3 differs from the verified security backport: " + name
            )


def package_hashes(site):
    package = site / "urllib3"
    if package.is_symlink():
        raise ValueError("Refusing a linked urllib3 package")
    files = list(package.rglob("*.py"))
    if not files or any(p.is_symlink() for p in files):
        raise ValueError("Missing or linked urllib3 source files")
    return {str(p.relative_to(package)): sha256(p) for p in sorted(files)}


def package_version(site, name):
    versions = [
        d.version
        for d in importlib.metadata.distributions(path=[str(site)])
        if d.metadata["Name"].lower() == name
    ]
    if len(versions) != 1:
        raise ValueError("Expected one installed " + name + " distribution")
    return versions[0]


def clean_environment(site, work):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PIP_", "PYTHON", "AWS_", "GH_", "GITHUB_", "GIT_"))
    }
    env.update(
        {
            "PIP_CONFIG_FILE": os.devnull,
            "PYTHONPATH": str(site),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "AWS_ACCESS_KEY_ID": "example-access-key",
            "AWS_SECRET_ACCESS_KEY": "example-secret-key",
            "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": str(work / "aws-config"),
            "AWS_SHARED_CREDENTIALS_FILE": str(work / "aws-credentials"),
            "AWS_CLI_AUTO_PROMPT": "off",
            "AWS_MAX_ATTEMPTS": "1",
            "AWS_REQUEST_CHECKSUM_CALCULATION": "when_required",
            "AWS_RESPONSE_CHECKSUM_VALIDATION": "when_required",
        }
    )
    return env


def probe(python, cli, site, aws_version, urllib_version, work):
    env = clean_environment(site, work)
    subprocess.run(
        [sys.executable, "-m", "pip", "--isolated", "--python", str(python), "check"],
        env=env,
        check=True,
    )
    identity = subprocess.check_output(
        [
            str(python),
            "-s",
            "-B",
            "-c",
            "import json,urllib3; "
            "print(json.dumps([urllib3.__version__,urllib3.__file__]))",
        ],
        env=env,
        text=True,
    )
    if json.loads(identity) != [urllib_version, str(site / "urllib3/__init__.py")]:
        raise ValueError("AWS CLI did not load the expected urllib3 package")
    command = [str(python), "-s", "-B", str(cli)]
    version = subprocess.check_output([*command, "--version"], env=env, text=True)
    if not version.startswith("aws-cli/" + aws_version + " "):
        raise ValueError("AWS CLI version changed")
    objects = {}
    observed = []

    class Endpoint(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_operation(self):
            # A bounded HTTP transport fixture, not an AWS authorization emulator.
            # Real cloud permissions and SigV4 verification are not inferred.
            if (
                self.path != "/example-bucket/smoke"
                or "Credential=example-access-key/"
                not in self.headers.get("Authorization", "")
            ):
                self.send_error(403)
                return
            observed.append(self.command)
            if self.command == "PUT":
                objects["smoke"] = self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("ETag", '"example-etag"')
            elif self.command == "DELETE":
                objects.pop("smoke", None)
                self.send_response(204)
            elif "smoke" not in objects:
                self.send_error(404)
                return
            else:
                self.send_response(200)
            content = objects.get("smoke", b"") if self.command == "GET" else b""
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if content:
                self.wfile.write(content)

        do_PUT = do_GET = do_DELETE = do_HEAD = handle_operation

    server = ThreadingHTTPServer(("127.0.0.1", 0), Endpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = work / "payload"
        payload.write_bytes(b"runner-image-aws-urllib3\n" * 64)
        output = work / "downloaded"
        endpoint = "http://127.0.0.1:" + str(server.server_port)
        options = [
            "--endpoint-url",
            endpoint,
            "--bucket",
            "example-bucket",
            "--key",
            "smoke",
        ]
        for operation, extra in (
            ("put-object", ["--body", str(payload)]),
            ("get-object", [str(output)]),
            ("delete-object", []),
        ):
            subprocess.run(
                [*command, "s3api", operation, *options, *extra],
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            if (
                operation == "get-object"
                and output.read_bytes() != payload.read_bytes()
            ):
                raise ValueError("AWS CLI local transfer bytes differ")
        result = subprocess.run(
            [*command, "s3api", "head-object", *options],
            env=env,
            capture_output=True,
            text=True,
        )
        if (
            result.returncode != 254
            or "404" not in result.stderr
            or objects
            or observed != ["PUT", "GET", "DELETE", "HEAD"]
        ):
            raise ValueError("AWS CLI local transfer/deletion lifecycle failed")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def build_wheel(work, python, plan):
    archive = work / "source.tar.gz"
    download(plan["source"]["url"], plan["source"]["sha256"], archive)
    with tarfile.open(archive) as source:
        source.extractall(work / "source", filter="data")
    root = work / "source/urllib3-2.6.3"
    for patch in plan["patches"]:
        target = work / (patch["commit"] + ".patch")
        url = "https://github.com/urllib3/urllib3/commit/" + patch["commit"] + ".patch"
        if patch["commit"] == "627636551e0e0159996b0f28dd21a60372cc5b10":
            url += "?full_index=1"
        download(url, patch["patch_sha256"], target)
        subprocess.run(
            ["git", "apply", "-C1", "--include=src/urllib3/*", str(target)],
            cwd=root,
            env={
                **clean_environment(root, work),
                "GIT_CEILING_DIRECTORIES": str(work),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            },
            check=True,
        )
    verify_modules(root / "src/urllib3", plan["modules"])
    # The sdist's cached metadata would suppress the downstream build identity.
    (root / "PKG-INFO").unlink()
    env = clean_environment(root, work)
    env["SETUPTOOLS_SCM_PRETEND_VERSION"] = build_version(plan)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "--no-cache-dir",
            "wheel",
            "--no-deps",
            "--index-url",
            "https://pypi.org/simple",
            "--wheel-dir",
            str(work / "wheels"),
            str(root),
        ],
        env=env,
        check=True,
    )
    wheels = list((work / "wheels").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected one backported urllib3 wheel")
    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata = "urllib3-" + build_version(plan) + ".dist-info/METADATA"
        if metadata not in wheel.namelist():
            raise ValueError("Built wheel omits the downstream version identity")
        identity = message_from_bytes(wheel.read(metadata))
        if identity["Name"] != "urllib3" or identity["Version"] != build_version(plan):
            raise ValueError("Built wheel version identity differs from source inputs")
        for name, digest in plan["modules"].items():
            if hashlib.sha256(wheel.read("urllib3/" + name)).hexdigest() != digest:
                raise ValueError("Built wheel differs from the verified backport")
    return wheels[0]


def layout(cellar):
    sites = list(cellar.glob("*/libexec/lib/python*/site-packages"))
    if len(sites) != 1:
        raise ValueError("Expected one installed AWS CLI Python tree")
    site = sites[0]
    if any(p.is_symlink() for p in (site, *site.parents)):
        raise ValueError("Refusing a linked AWS CLI package tree")
    return site, site.parents[2] / "bin/python", site.parents[3] / "bin/aws"


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


def save_receipt(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(temporary, path)


def verify_installed(cellar, receipt):
    site, python, cli = layout(cellar)
    report = json.loads(receipt.read_text())
    plan = source_inputs()
    version = package_version(site, "urllib3")
    if (
        report["source_inputs"] != plan
        or report["aws_version"] != package_version(site, "awscli")
        or report["urllib3_version"] != version
        or report["package_sha256"] != package_hashes(site)
    ):
        raise ValueError("AWS CLI build receipt does not match installed source")
    if report["status"] == "backported":
        if version != build_version(plan):
            raise ValueError("AWS CLI backport identity mismatch")
        verify_modules(site / "urllib3", plan["modules"])
    elif (
        report["status"] != "upstream"
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
        or tuple(map(int, version.split("."))) < (2, 8, 0)
    ):
        raise ValueError("Unrecognized AWS urllib3 build")
    with tempfile.TemporaryDirectory(prefix="runner-aws-probe-") as d:
        probe(python, cli, site, report["aws_version"], version, Path(d))
    print(json.dumps(report, sort_keys=True))


def refresh(cellar, receipt):
    site, python, cli = layout(cellar)
    plan = source_inputs()
    version = package_version(site, "urllib3")
    aws_version = package_version(site, "awscli")
    if version == build_version(plan):
        verify_installed(cellar, receipt)
        return
    upstream = bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)) and tuple(
        map(int, version.split("."))
    ) >= (2, 8, 0)
    if not upstream and version != "2.6.3":
        raise ValueError(
            "Reassess AWS CLI's changed urllib3 dependency before backporting"
        )
    with tempfile.TemporaryDirectory(prefix=".runner-image-aws-", dir=site.parent) as d:
        work = Path(d)
        report = {
            "source_inputs": plan,
            "aws_version": aws_version,
            "urllib3_version": version if upstream else build_version(plan),
            "status": "upstream" if upstream else "backported",
        }
        if upstream:
            probe(python, cli, site, aws_version, version, work)
            report["package_sha256"] = package_hashes(site)
            save_receipt(receipt, report)
        else:
            wheel = build_wheel(work, python, plan)
            replacement = work / "site-packages"
            shutil.copytree(site, replacement, symlinks=True)
            metadata = list(replacement.glob("urllib3-*.dist-info"))
            if len(metadata) != 1 or metadata[0].is_symlink():
                raise ValueError("Expected one unlinked urllib3 metadata directory")
            shutil.rmtree(replacement / "urllib3")
            shutil.rmtree(metadata[0])
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-deps",
                    "--no-compile",
                    "--target",
                    str(replacement),
                    str(wheel),
                ],
                env=clean_environment(replacement, work),
                check=True,
            )
            verify_modules(replacement / "urllib3", plan["modules"])
            report["wheel_sha256"] = sha256(wheel)
            report["package_sha256"] = package_hashes(replacement)
            probe(
                python, cli, replacement, aws_version, report["urllib3_version"], work
            )

            def verify():
                probe(python, cli, site, aws_version, report["urllib3_version"], work)
                if package_hashes(site) != report["package_sha256"]:
                    raise ValueError("Activated AWS urllib3 source changed")
                save_receipt(receipt, report)

            activate(site, replacement, work / "previous", verify)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["refresh"], ["verify"]):
        raise ValueError("Expected refresh or verify")
    action = (
        verify_installed if len(sys.argv) == 2 and sys.argv[1] == "verify" else refresh
    )
    action(
        Path("/opt/homebrew/Cellar/awscli"),
        Path.home() / ".runner-image/awscli-urllib3-build.json",
    )
