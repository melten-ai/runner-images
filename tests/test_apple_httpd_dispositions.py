"""Check that Apple httpd module dispositions fail closed when modules change."""

import json
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("change", [None, "ldap", "proxy", "http2"])
def test_apple_httpd_module_predicate(monkeypatch, capsys, change):
    source = (
        Path(__file__).resolve().parents[1] / "macos/image/verify-toolchain.sh"
    ).read_text()
    code = source.split("<<'APPLE_HTTPD'\n", 1)[1].split("\nAPPLE_HTTPD", 1)[0]

    modules = ["core", "so", "http", "mpm_prefork"]
    if change:
        modules.append(change)
    output = "Loaded Modules:\n" + "".join(
        f" {module}_module (static)\n" for module in modules
    )

    def check_output(command, *, text, stderr):
        assert command == ["/usr/sbin/httpd", "-M"]
        assert text and stderr == subprocess.STDOUT
        return output

    monkeypatch.setattr(subprocess, "check_output", check_output)
    if change:
        with pytest.raises(RuntimeError, match="modules loaded"):
            exec(compile(code, "apple-httpd-probe", "exec"), {})
        assert not capsys.readouterr().out
    else:
        exec(compile(code, "apple-httpd-probe", "exec"), {})
        report = json.loads(capsys.readouterr().out)
        assert report["apple_httpd_dispositions"] == "verified"
        assert report["loaded_modules"] == ["core", "http", "mpm_prefork", "so"]
