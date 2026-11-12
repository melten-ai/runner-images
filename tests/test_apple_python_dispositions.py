"""Runtime predicates must reject an invalidated Apple Python disposition."""

import _bz2
import bz2
import ctypes
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "change",
    [
        None,
        "python-version",
        "module-location",
        "data_filter",
        "tar_filter",
        "_NAMED_FILTERS",
        "extraction_filter",
        "extract-filter",
        "extractall-filter",
        "linkage",
        "library-version",
        "roundtrip",
    ],
)
def test_apple_python_runtime_predicates(monkeypatch, capsys, change):
    source = (
        Path(__file__).resolve().parents[1] / "macos/image/verify-toolchain.sh"
    ).read_text()
    code = source.split("<<'APPLE_PYTHON'\n", 1)[1].split("\nAPPLE_PYTHON", 1)[0]
    monkeypatch.setattr(sys, "version_info", (3, 9, 6))
    monkeypatch.setattr(
        _bz2, "__file__", str(Path(sys.base_prefix) / "lib/_bz2.so"), raising=False
    )
    for name in ("data_filter", "tar_filter", "_NAMED_FILTERS"):
        monkeypatch.delattr(tarfile, name, raising=False)
    monkeypatch.setattr(
        tarfile,
        "TarFile",
        type(
            "TarFile",
            (),
            {
                "extract": lambda self, member, path=None: None,
                "extractall": lambda self, path=None: None,
            },
        ),
    )

    def linked(command, *, text):
        assert command == ["/usr/bin/otool", "-L", _bz2.__file__]
        assert text
        library = (
            "/usr/local/lib/libbz2.1.0.dylib"
            if change == "linkage"
            else "/usr/lib/libbz2.1.0.dylib"
        )
        return f"example-module:\n\t{library} (compatibility version 1.0.0)\n"

    def load_library(path):
        assert path == "/usr/lib/libbz2.1.0.dylib"

        def version():
            return b"1.0.6, example" if change == "library-version" else b"1.0.8"

        return SimpleNamespace(BZ2_bzlibVersion=version)

    monkeypatch.setattr(subprocess, "check_output", linked)
    monkeypatch.setattr(ctypes, "CDLL", load_library)
    if change == "python-version":
        monkeypatch.setattr(sys, "version_info", (3, 9, 7))
    elif change == "module-location":
        monkeypatch.setattr(_bz2, "__file__", "/example/other-python/_bz2.so")
    elif change in ("data_filter", "tar_filter", "_NAMED_FILTERS"):
        monkeypatch.setattr(tarfile, change, object(), raising=False)
    elif change == "extraction_filter":
        monkeypatch.setattr(
            tarfile.TarFile, "extraction_filter", object(), raising=False
        )
    elif change in ("extract-filter", "extractall-filter"):
        method = "extract" if change == "extract-filter" else "extractall"
        monkeypatch.setattr(
            tarfile.TarFile, method, lambda self, path=None, *, filter=None: None
        )
    elif change == "roundtrip":
        monkeypatch.setattr(bz2, "decompress", lambda data: b"incorrect bytes")
    if change is not None:
        with pytest.raises((RuntimeError, ValueError)):
            exec(compile(code, "apple-python-probe", "exec"), {})
        assert not capsys.readouterr().out
    else:
        exec(compile(code, "apple-python-probe", "exec"), {})
        report = json.loads(capsys.readouterr().out)
        assert report["apple_python_dispositions"] == "verified"
        assert report["tarfile_extraction_filters"] is False
        assert report["bzip2_version"] == "1.0.8"
        assert report["bzip2_roundtrip"] == "passed"
