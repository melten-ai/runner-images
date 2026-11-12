"""Check curl disposition predicates against changed libcurl API observations."""

import ctypes
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "change",
    [
        None,
        "linkage",
        "null-version",
        "version",
        "backend-enumeration",
        "null-backends",
        "empty-backends",
        "wolfssl",
        "gnutls",
        "unknown-backend",
        "null-protocols",
        "empty-protocols",
        "sftp",
        "scp",
        "null-zlib",
        "malformed-zlib",
        "old-zlib",
        "boundary-zlib",
    ],
)
def test_apple_curl_runtime_predicates(monkeypatch, capsys, change):
    source = (
        Path(__file__).resolve().parents[1] / "macos/image/verify-toolchain.sh"
    ).read_text()
    code = source.split("<<'APPLE_CURL'\n", 1)[1].split("\nAPPLE_CURL", 1)[0]
    retained = []

    def linked(command, *, text):
        assert command == ["/usr/bin/otool", "-L", "/usr/bin/curl"]
        assert text
        library = (
            "/example/libcurl.4.dylib"
            if change == "linkage"
            else "/usr/lib/libcurl.4.dylib"
        )
        return f"example-curl:\n\t{library} (compatibility version 12.0.0)\n"

    def load_library(path):
        assert path == "/usr/lib/libcurl.4.dylib"

        def get_info(age):
            assert age == 0
            if change == "null-version":
                return get_info.restype()
            protocols = [b"file", b"http", b"https"]
            if change in ("sftp", "scp"):
                protocols.append(change.encode())
            elif change == "empty-protocols":
                protocols = []
            array = (ctypes.c_char_p * (len(protocols) + 1))(*protocols, None)
            zlib = {
                "null-zlib": None,
                "malformed-zlib": b"1.2.12-unrecognized",
                "old-zlib": b"1.2.0.3",
                "boundary-zlib": b"1.2.0.4",
            }.get(change, b"1.2.12")
            info = get_info.restype._type_(
                version=b"8.7.2" if change == "version" else b"8.7.1",
                libz_version=zlib,
                protocols=None if change == "null-protocols" else array,
            )
            retained.extend([array, info])
            return ctypes.pointer(info)

        def sslset(identifier, name, target):
            assert identifier == 0 and name is None
            if change == "backend-enumeration":
                return 2
            if change == "null-backends":
                return 1
            pointer = sslset.argtypes[2]._type_
            backend = pointer._type_._type_
            names = ["openssl", "secure-transport"]
            if change in ("wolfssl", "gnutls", "unknown-backend"):
                names.append(change)
            elif change == "empty-backends":
                names = []
            values = [
                backend(id=index, name=n.encode()) for index, n in enumerate(names)
            ]
            array = (ctypes.POINTER(backend) * (len(values) + 1))(
                *(ctypes.pointer(value) for value in values), None
            )
            retained.extend([*values, array])
            ctypes.cast(target, sslset.argtypes[2])[0] = ctypes.cast(array, pointer)
            return 1

        return SimpleNamespace(curl_version_info=get_info, curl_global_sslset=sslset)

    monkeypatch.setattr(subprocess, "check_output", linked)
    monkeypatch.setattr(ctypes, "CDLL", load_library)
    if change not in (None, "boundary-zlib"):
        with pytest.raises(RuntimeError):
            exec(compile(code, "apple-curl-probe", "exec"), {})
        assert not capsys.readouterr().out
    else:
        exec(compile(code, "apple-curl-probe", "exec"), {})
        report = json.loads(capsys.readouterr().out)
        assert report["apple_curl_dispositions"] == "verified"
        assert report["tls_backends"] == ["openssl", "secure-transport"]
        assert report["protocols"] == ["file", "http", "https"]
        assert report["zlib_version"] == (
            "1.2.0.4" if change == "boundary-zlib" else "1.2.12"
        )
