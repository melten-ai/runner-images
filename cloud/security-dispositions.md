# Cloud vulnerability dispositions

High/Critical findings outside explicit dispositions and the [owner-accepted Node 20 legacy-runtime risks](../security-exceptions.md) block cloud publication. The scanner retains each disposition and its reason in `ignoredMatches`; a disposition does not remove the kernel from the SBOM, and the release scan uses the qualified build's original source policy.

## Supplier-fixed Ubuntu kernel backports

The scanner matches NVD linux-kernel advisories against the Ubuntu kernel's upstream base version, but Canonical backports security fixes into its own package revisions, so a fixed kernel still reports the original advisories. [`kernel-fixes.json`](kernel-fixes.json) records, per CVE, the exact Canonical fixed version for the installed source package, the confirming noble USN release row, and the SHA-256 of the Canonical tracker payload (`https://ubuntu.com/security/cves/<CVE>.json`). The tracker content is Canonical-served over HTTPS only; its cryptographic signature chain was **not** verified. Every dispositioned CVE satisfies both conditions on its platform: the tracker fix is released at or before the installed version, **and** an embedded noble USN release-package row confirms exactly that fixed version.

| Platform | Kernel | Binary/source identity | Version | Dispositioned CVEs |
| --- | --- | --- | --- | --- |
| GCP | `7.0.0-1017-gcp` | `linux-image-7.0.0-1017-gcp` (`linux-signed-gcp-7.0`), `linux-modules-7.0.0-1017-gcp` (`linux-gcp-7.0`) | `7.0.0-1017.19~24.04.1` | 758 |
| Azure | `7.0.0-1017-azure` | `linux-image-7.0.0-1017-azure` (`linux-signed-azure-7.0`), `linux-modules-7.0.0-1017-azure` (`linux-azure-7.0`) | `7.0.0-1017.17~24.04.1` | 738 |

The expected `vmlinuz` and modules-`md5sums` digests in the manifest equal the corresponding members of the official Ubuntu archive `.deb` packages for exactly those versions. Running-kernel, dpkg-identity, source-package and MD5 verification were proved on cold-booted exact images: the GCP guest compared its kernel byte-for-byte against the official download in the guest; the Azure VM blocked public egress, so the official-archive comparison ran externally against the native SHA-256, and an offline unprivileged native smoke ran through Azure RunCommand (not an Actions job). The module files passed `dpkg --verify` and their md5sums control data matches the official archive, so the binding covers modules as well as `vmlinuz`. No claim is made about runtime module absence or loaded-code absence.

## Native binding on every build

A version string alone does not establish these dispositions. Before SBOM collection, [`linux/verify-kernel.py`](linux/verify-kernel.py) runs read-only inside the provisioning VM, with no network, for each manifest entry whose bound `/boot/vmlinuz-<release>` is present:

- the file must be a regular, non-symlinked file with exactly the bound SHA-256;
- `/etc/os-release` must identify Ubuntu 24.04;
- `dpkg -S` must attribute the file to the exact image package, and `dpkg-query` must report both image and modules packages `installed` at the exact binary version with the exact source package and source version;
- the modules package's `md5sums` control file must be a regular file with exactly the bound SHA-256, and `dpkg --verify` for both packages must exit cleanly with no output;
- native `dpkg --compare-versions` must confirm the installed version is `>=` every distinct supplier fixed version;
- when `uname -r` reports the bound ABI as the running kernel, `/proc/version` must identify it too.

Any present-but-changed byte, identity or version fails qualification before any SBOM or scan evidence exists. The structured observations are printed into `toolchain-check.txt`, whose hash is bound into the qualification receipt. If the bound kernel file is absent (a newer base kernel), the entry is inactive: every ignore rule is pinned to the exact `linux-kernel` package version and `boot/vmlinuz-<release>` location under `nvd:cpe`, so no rule can match another kernel, and a new or unbound kernel keeps all of its findings and blocks publication until it is reassessed. Absence of the old file is never treated as evidence that a new kernel is fixed, and no future kernel is waived.

## What remains blocking

The per-platform released-and-USN-confirmed fixes above are the canonical supplier-fix dispositions. Separate owner-approved retention rules in `cloud/grype.json` cover only exact provider-managed or required identities that remain after remediation. Every such rule is pinned to advisory, package name, version, type and location and is checked for anchored names and exact version/location values.

Everything else keeps its findings, including:

- the 20 Azure advisories whose fix (`linux-azure-7.0 7.0.0-1020.21~24.04.1`) is still `pending` without a released noble USN — notably **CVE-2026-98432** and **CVE-2026-100075**. These remain blocking unless an exact owner-approved retention rule names the same advisory, package identity, version, type and location. Reassess those rules when Canonical publishes the corresponding noble USN;
- the 103 `not-affected` tracker annotations (vendor convention, requires review — not dispositioned);
- the 11 unknown/missing/deferred tracker cases;
- any other package name, type, version, location or namespace, and all non-kernel High/Critical findings outside the exact owner-approved retention rules in `cloud/grype.json`.

These are supplier-fix dispositions with native binding, not risk acceptances.

## Mechanical coupling

The canonical `cloud/grype.json` kernel rules are derived from `kernel-fixes.json` by [`check-kernel-security.py`](check-kernel-security.py); owner-approved retention rules are validated separately for exact scope. Any drift between data, rules and the preserved Node 20 rules fails the check. Run `python3 cloud/check-kernel-security.py` with Grype installed: the actual scanner must first report every canonical dispositioned advisory for the exact bound kernel artifacts, then move only those identities and the explicitly matched exact retention rules to `ignoredMatches`, while adjacent names, versions, package types, locations and a newer unbound ABI retain their findings and High/Critical still returns exit 2. The same regression runs in CI, and `cloud/kernel-fixes.json` is part of the cloud build-input fingerprint, so changed disposition data produces a new image identity.
