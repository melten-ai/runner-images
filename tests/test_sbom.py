"""Behavioral contract for SPDX versus native SBOM package inventory."""

import pytest

from runner_images.qualification import verify_sbom_packages

ROOT_SPDX_ID = "SPDXRef-DocumentRoot-Directory-example"
DESCRIBES_ROOT = {
    "spdxElementId": "SPDXRef-DOCUMENT",
    "relationshipType": "DESCRIBES",
    "relatedSpdxElement": ROOT_SPDX_ID,
}


def native(*artifacts, source=None):
    doc = {"artifacts": list(artifacts)}
    if source is not None:
        doc["source"] = source
    return doc


def spdx(*packages, relationships=(), **metadata):
    return {
        "spdxVersion": "SPDX-2.3",
        "creationInfo": {"created": "2019-06-06T00:00:00Z"},
        "documentNamespace": "https://example.org/synthetic-spdx",
        "name": "synthetic-document",
        "packages": list(packages),
        "relationships": list(relationships),
        **metadata,
    }


def artifact(name, version=None):
    row = {"name": name}
    if version is not None:
        row["version"] = version
    return row


def package(name, version_info=None, spdx_id=None):
    row = {"name": name}
    if version_info is not None:
        row["versionInfo"] = version_info
    if spdx_id is not None:
        row["SPDXID"] = spdx_id
    return row


def directory_scan(*artifacts):
    """Native inventory from dir:/example with SPDX root package noise."""
    root = package("/example", "", ROOT_SPDX_ID)
    return (
        native(
            *artifacts,
            source={"type": "directory", "name": "/example", "version": ""},
        ),
        spdx(
            root,
            *(package(a["name"], a.get("version", "")) for a in artifacts),
            relationships=[DESCRIBES_ROOT],
        ),
    )


def test_matching_inventories_ignore_order_and_document_metadata():
    verify_sbom_packages(
        native(
            artifact("alpha", "1"),
            artifact("beta"),
            artifact("gamma", "2.0"),
        ),
        spdx(
            package("gamma", "2.0"),
            package("beta"),
            package("alpha", "1"),
        ),
    )


def test_default_empty_versions_and_duplicate_counts_must_match():
    verify_sbom_packages(
        native(artifact("dup"), artifact("dup"), artifact("solo", "3")),
        spdx(
            package("dup"),
            package("dup"),
            package("solo", "3"),
        ),
    )
    with pytest.raises(ValueError):
        verify_sbom_packages(
            native(artifact("dup"), artifact("dup")),
            spdx(package("dup")),
        )


@pytest.mark.parametrize(
    "native_doc,spdx_doc",
    [
        (
            native(artifact("only-native", "1")),
            spdx(package("only-native", "1"), package("extra", "9")),
        ),
        (
            native(artifact("left", "1"), artifact("right", "2")),
            spdx(package("left", "1")),
        ),
        (
            native(artifact("name", "1")),
            spdx(package("name", "2")),
        ),
        (
            native(artifact("expected", "1")),
            spdx(package("other", "1")),
        ),
    ],
)
def test_inventory_mismatch_raises(native_doc, spdx_doc):
    with pytest.raises(ValueError):
        verify_sbom_packages(native_doc, spdx_doc)


def test_syft_directory_root_excluded_only_with_document_describes():
    native_doc, spdx_doc = directory_scan(artifact("example-runtime", "1.2.3"))
    verify_sbom_packages(native_doc, spdx_doc)

    reordered = spdx(
        package("example-runtime", "1.2.3"),
        package("/example", "", ROOT_SPDX_ID),
        relationships=[DESCRIBES_ROOT],
    )
    verify_sbom_packages(native_doc, reordered)

    without_source = native(artifact("example-runtime", "1.2.3"))
    with pytest.raises(ValueError):
        verify_sbom_packages(without_source, spdx_doc)

    wrong_type = native(
        artifact("example-runtime", "1.2.3"),
        source={"type": "image", "name": "/example"},
    )
    with pytest.raises(ValueError):
        verify_sbom_packages(wrong_type, spdx_doc)


@pytest.mark.parametrize(
    "relationships,root_name,root_version",
    [
        (
            [
                {
                    "spdxElementId": "SPDXRef-OTHER",
                    "relationshipType": "DESCRIBES",
                    "relatedSpdxElement": ROOT_SPDX_ID,
                }
            ],
            "/example",
            "",
        ),
        ([DESCRIBES_ROOT], "/wrong-root", ""),
        ([DESCRIBES_ROOT], "/example", "1.0"),
        (
            [
                {
                    "spdxElementId": "SPDXRef-DOCUMENT",
                    "relationshipType": "DESCRIBES",
                    "relatedSpdxElement": "SPDXRef-not-the-directory-root",
                }
            ],
            "/example",
            "",
        ),
        ([], "/example", ""),
    ],
)
def test_malformed_or_nonroot_describes_never_hides_packages(
    relationships, root_name, root_version
):
    native_doc = native(
        artifact("example-runtime", "1.2.3"),
        source={"type": "directory", "name": "/example", "version": ""},
    )
    spdx_doc = spdx(
        package(root_name, root_version, ROOT_SPDX_ID),
        package("example-runtime", "1.2.3"),
        relationships=relationships,
    )
    with pytest.raises(ValueError):
        verify_sbom_packages(native_doc, spdx_doc)


def test_only_one_directory_root_removed_when_name_versions_repeat():
    native_doc = native(
        artifact("example-runtime", "1.2.3"),
        source={"type": "directory", "name": "/example", "version": ""},
    )
    spdx_doc = spdx(
        package("/example", "", ROOT_SPDX_ID),
        package("/example", "", "SPDXRef-duplicate-root"),
        package("example-runtime", "1.2.3"),
        relationships=[DESCRIBES_ROOT],
    )
    with pytest.raises(ValueError):
        verify_sbom_packages(native_doc, spdx_doc)


def test_directory_entry_without_document_root_ids_is_not_excluded():
    native_doc = native(
        artifact("example-runtime", "1.2.3"),
        source={"type": "directory", "name": "/example", "version": ""},
    )
    spdx_doc = spdx(
        package("/example"),
        package("example-runtime", "1.2.3"),
        relationships=[
            {"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES"}
        ],
    )
    with pytest.raises(ValueError, match="inventories differ"):
        verify_sbom_packages(native_doc, spdx_doc)


@pytest.mark.parametrize(
    "native_doc,spdx_doc",
    [
        (native(), spdx(package("only-spdx"))),
        (native(artifact("only-native")), spdx()),
        ({}, spdx(package("x"))),
        (native(artifact("x")), {}),
        (native(artifact("x")), {"packages": "not-a-list"}),
        ({"artifacts": "not-a-list"}, spdx(package("x"))),
        (native({"name": ""}), spdx(package("x"))),
        ({"artifacts": [None]}, spdx(package("x"))),
        (native(artifact("x")), {"packages": [42]}),
        (native(artifact("x")), {"packages": [{"versionInfo": "1"}]}),
        (native(artifact("x", 1)), spdx(package("x", "1"))),
        (native(artifact("x")), spdx(package(123, "1"))),
    ],
)
def test_invalid_inventories_raise(native_doc, spdx_doc):
    with pytest.raises(ValueError):
        verify_sbom_packages(native_doc, spdx_doc)
