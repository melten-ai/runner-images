# Mac vulnerability dispositions

High/Critical findings block publication. The scanner retains each accepted disposition and its reason in `ignoredMatches`; a disposition does not remove a package from the SBOM. The qualified source revision supplies the release scan policy.

In addition to the platform-specific Python rules in [grype.json](grype.json), two rules cover fixes already present in exact Homebrew releases:

| Finding | Installed release | Evidence |
| --- | --- | --- |
| CVE-2017-5506 | ImageMagick 7.1.2-35 | The [upstream fix](https://github.com/ImageMagick/ImageMagick/commit/9a069e0f2e027ec5138f998023cf9cb62c04889f) rejects a negative EXIF entry offset before accessing profile memory. The same guard is present in [this release's `SyncExifProfile`](https://github.com/ImageMagick/ImageMagick/blob/7.1.2-35/MagickCore/profile.c). |
| CVE-2025-32460 | GraphicsMagick 1.3.49 | The [supplier fix](https://foss.heptapod.net/graphicsmagick/graphicsmagick/-/commit/8e56520435df50f618a03f2721a39a70a515f1cb) selects two JXL output channels for grayscale with alpha: `pixel_format.num_channels=image->matte ? 2 : 1`. That exact statement is present in `coders/jxl.c` of the official 1.3.49 source archive. [Debian independently records this release as fixed](https://security-tracker.debian.org/tracker/CVE-2025-32460). |

The installed ImageMagick version reports source commit `acaf7e418d4ab0386e165251b7ef014a7959a205`; its [exact source](https://github.com/ImageMagick/ImageMagick/blob/acaf7e418d4ab0386e165251b7ef014a7959a205/MagickCore/profile.c) also contains the guard.

The GraphicsMagick archive was downloaded from the [official release distribution](https://downloads.sourceforge.net/project/graphicsmagick/graphicsmagick/1.3.49/GraphicsMagick-1.3.49.tar.xz) and checked against Homebrew's published SHA256: `7efa070dc31116b4315061b39f84bc7181e8b060bf61214ec9af851131af9c81`. Installed binary version output and Homebrew installation receipts were checked in an isolated guest. These are version/source-based dispositions, not a general exemption for either project or for Homebrew packages.

Rules require the exact package name, version, `homebrew` package type and Cellar formula-record location. A new version requires a new assessment. Other findings on the same packages remain blocking, including findings without a known fix. No runtime feature or exploitability assumption is used to waive a broader package range.

Run `uv run python scripts/check-macos-security.py` with Grype installed. The actual scanner must first report all disposition fixtures, then move only the accepted identities to `ignoredMatches`. Adjacent names, versions, package types and locations must retain their original findings, and High/Critical must still return exit 2.

## Apple `ldapsearch` server-only matches

The exact `openldap` 2.4.28 `binary` record at `usr/bin/ldapsearch` is Apple's
client executable (`openldap-search-binary`), not an OpenLDAP `slapd` server.
The image contains no `slapd` executable or server backend. The policy therefore
disposes only the server-side findings below at that exact package, version and
path; client-side library findings remain blocking:

`CVE-2012-1164`, `CVE-2013-4449`, `CVE-2014-9713`, `CVE-2015-1545`,
`CVE-2017-14159`, `CVE-2017-17740`, `CVE-2017-9287`, `CVE-2019-13057`,
`CVE-2019-13565`, `CVE-2020-12243`, `CVE-2020-25692`, `CVE-2020-25709`,
`CVE-2020-25710`, `CVE-2020-36221`, `CVE-2020-36222`, `CVE-2020-36223`,
`CVE-2020-36224`, `CVE-2020-36225`, `CVE-2020-36226`, `CVE-2020-36227`,
`CVE-2020-36228`, `CVE-2020-36229`, `CVE-2020-36230`, `CVE-2021-27212`,
`CVE-2022-29155`.

The advisory descriptions identify these as `slapd` server code, server-only
overlays or server-only backends. The policy keeps the client-side TLS, BER and
certificate findings (`CVE-2012-2668`, `CVE-2015-3276`, `CVE-2015-6908` and
`CVE-2020-15719`) blocking. The exact package/location rule and adjacent-identity
regression are covered by [grype.json](grype.json) and
`uv run python scripts/check-macos-security.py`; the OpenLDAP server/client
boundary is described in the [OpenLDAP administrator guide](https://www.openldap.org/doc/admin26/quickstart.html).

## Apple Python runtime predicates

Seven additional rules apply only to Python 3.9.6 at the two exact Apple app-bundle locations already named in the policy. A version string alone does not establish these dispositions. Before SBOM collection, `verify-toolchain.sh` executes each bundle's own interpreter in isolated mode, checks its standard-library module locations and enforces these predicates:

- [CVE-2025-4517](https://www.cve.org/CVERecord?id=CVE-2025-4517), [CVE-2025-4138](https://www.cve.org/CVERecord?id=CVE-2025-4138), [CVE-2025-4330](https://www.cve.org/CVERecord?id=CVE-2025-4330), [CVE-2025-4435](https://www.cve.org/CVERecord?id=CVE-2025-4435), [CVE-2026-11940](https://www.cve.org/CVERecord?id=CVE-2026-11940) and [CVE-2026-82049](https://www.cve.org/CVERecord?id=CVE-2026-82049) concern tarfile extraction filters. These two Apple runtimes have neither the filter functions/registry nor the `extract`/`extractall` filter arguments or class extraction-filter hook. The verifier rejects the image if any appears. This does not make unfiltered extraction of untrusted archives safe; other archive-extraction findings remain blocking.
- [CVE-2019-12900](https://www.cve.org/CVERecord?id=CVE-2019-12900) affects bzip2 through 1.0.6. Each runtime's actual `_bz2` extension must link `/usr/lib/libbz2.1.0.dylib`; the library must report 1.0.8. The verifier also performs a real compression/decompression roundtrip. A changed link, version or failed roundtrip rejects the image.

The runtime observations are written into `toolchain-check.txt`, whose hash is bound into qualification and rechecked for release. Missing interpreters, changed Python versions and invalidated predicates fail the build before qualification. The hosted release scans with that qualified build's original source policy. The runtime probes do not alter the Apple bundles or add extraction-filter functionality.

## Apple curl runtime predicates

Seven rules apply only to the `binary` package `curl` 8.7.1 at `usr/bin/curl`. Before qualification, the verifier executes an isolated Apple Python probe under both ARM and Intel architectures, checks the executable's system-libcurl linkage and queries the actual loaded `/usr/lib/libcurl.4.dylib` through its public API. Rosetta execution must work for the Intel probe; a missing or failed architecture rejects the build.

| Findings | Required observation |
| --- | --- |
| [CVE-2026-87866](https://curl.se/docs/CVE-2026-87866.html) | The wolfSSL-only defect cannot apply: every selectable TLS backend must be OpenSSL or Secure Transport. |
| [CVE-2026-107928](https://curl.se/docs/CVE-2026-107928.html) | The GnuTLS-only defect cannot apply under the same complete backend enumeration. |
| [CVE-2025-0725](https://curl.se/docs/CVE-2025-0725.html) | Runtime zlib must be at least 1.2.0.4; the supplier limits this issue to 1.2.0.3 and older. |
| [CVE-2026-87865](https://curl.se/docs/CVE-2026-87865.html), [CVE-2026-9547](https://curl.se/docs/CVE-2026-9547.html), [CVE-2026-86410](https://curl.se/docs/CVE-2026-86410.html), [CVE-2026-12064](https://curl.se/docs/CVE-2026-12064.html) | The linked library must expose neither SFTP nor SCP. These advisories require SSH transfers; they concern libssh2, libssh or SSH options in the command-line tool. |

Checking only the default backend, or setting `CURL_SSL_BACKEND` and looking at `curl --version`, does not enumerate every available backend. The verifier uses `curl_global_sslset` to obtain the complete backend list and `curl_version_info` for the linked library version, supported protocols and runtime zlib. Missing, empty or unrecognized observations fail verification; future versions or changed predicates require reassessment. Observations for both architectures are retained in the same qualification-hashed native-check evidence.

Cookie, Public Suffix List, authentication, proxy and HTTP/2 findings remain blocking. In particular, absence of PSL support does not establish protection against overly broad cookies and is not a disposition for those findings.

## AWS CLI urllib3 backport

AWS CLI 2.39.6 requires urllib3 `>=1.25.4,<=2.6.3`. The factory preserves that requirement and the official CLI release, backporting supplier security fixes into `2.6.3+melten.9ff67c841cad`. A PEP 440 local version satisfies the existing upper bound. This compatibility backport is tracked in [#8](https://github.com/melten-ai/runner-images/issues/8); it is skipped once the vendor installs a supported upstream version with these fixes. The build identity derives from the source archive, patch checksums and verified resulting modules in [refresh-aws-cli.py](image/refresh-aws-cli.py).

Four rules cover only that exact local Python distribution at the AWS CLI 2.39.6 / Python 3.15 Cellar metadata location:

- [GHSA-mf9v-mfxr-j63j](https://github.com/advisories/GHSA-mf9v-mfxr-j63j): bounded decompression during repeated streaming reads and draining.
- [GHSA-qccp-gfcp-xxvc](https://github.com/advisories/GHSA-qccp-gfcp-xxvc): stripping sensitive headers from cross-origin low-level proxy redirects.
- [GHSA-vxq7-64xx-v4gw](https://github.com/advisories/GHSA-vxq7-64xx-v4gw): bounded chunk-size and trailer-line reads.
- [GHSA-8988-9cw3-xx77](https://github.com/advisories/GHSA-8988-9cw3-xx77): separation of proxy and target TLS settings.

Source and patch downloads require their recorded SHA256. Patching is isolated from any enclosing Homebrew Git checkout; exact resulting module hashes must match the tested backport before wheel construction and again after installation. The sdist's cached core metadata is discarded so the build receives its distinct local identity. A wheel missing that identity is rejected. Other AWS dependencies are preserved, and `pip check` runs against the actual vendor interpreter with the candidate package tree.

Before and after activation, the actual CLI writes an object to an isolated local HTTP fixture, downloads matching bytes, deletes it and observes the missing object. Only synthetic credentials and the fixed test bucket/key are accepted. This tests the HTTP transport and material file lifecycle; the fixture is not an AWS permission or SigV4 emulator and does not establish live cloud authorization. Activation or receipt-write failure restores the original package tree. Replay verifies the existing source hashes and identity without rebuilding.

The receipt records source inputs, wheel SHA256 and installed Python-file hashes. The factory independently verifies it before SBOM collection and includes the resulting report in qualification-hashed native-check evidence. The scanner retains the four source-fixed dispositions with reasons. Ordinary unpatched 2.6.3, other names, versions and locations retain their existing findings. Installed upstream urllib3 2.8.0 or newer is verified without applying this backport or these local-build exceptions.
