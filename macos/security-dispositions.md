# Mac vulnerability dispositions

High/Critical findings outside explicit dispositions and [owner-accepted legacy-runtime risks](../security-exceptions.md) block publication. The scanner retains each accepted disposition and its reason in `ignoredMatches`; a disposition does not remove a package from the SBOM. The qualified source revision supplies the release scan policy.

In addition to the platform-specific Python rules in [grype.json](grype.json), four rules cover fixes already present in exact Homebrew releases:

| Finding | Installed release | Evidence |
| --- | --- | --- |
| CVE-2017-5506 | ImageMagick 7.1.2-35 | The [upstream fix](https://github.com/ImageMagick/ImageMagick/commit/9a069e0f2e027ec5138f998023cf9cb62c04889f) rejects a negative EXIF entry offset before accessing profile memory. The same guard is present in [this release's `SyncExifProfile`](https://github.com/ImageMagick/ImageMagick/blob/7.1.2-35/MagickCore/profile.c). |
| CVE-2025-32460 | GraphicsMagick 1.3.49 | The [supplier fix](https://foss.heptapod.net/graphicsmagick/graphicsmagick/-/commit/8e56520435df50f618a03f2721a39a70a515f1cb) selects two JXL output channels for grayscale with alpha: `pixel_format.num_channels=image->matte ? 2 : 1`. That exact statement is present in `coders/jxl.c` of the official 1.3.49 source archive. [Debian independently records this release as fixed](https://security-tracker.debian.org/tracker/CVE-2025-32460). |

| CVE-2014-9826 | ImageMagick 7.1.2-35 | [Upstream patch 69490f5](https://sources.debian.org/src/imagemagick/8%3A6.8.9.9-5/debian/patches/0025-Fix-handling-of-corrupted-of-psd-sun-and-xpm-file.patch/) changes two SUN header errors from `CoderError` to `CorruptImageError`. Both guards are present in the installed release's [exact source](https://github.com/ImageMagick/ImageMagick/blob/acaf7e418d4ab0386e165251b7ef014a7959a205/coders/sun.c). [Debian records this release line as fixed](https://security-tracker.debian.org/tracker/CVE-2014-9826). |
| CVE-2007-0770 | GraphicsMagick 1.3.49 | The official archive's `ChangeLog.2007` records the 2007-02-10 fix: avoid evaluating `ReadBlobByte()` twice inside `Min()`. In `coders/palm.c`, the RLE loop reads the run length into `count`, checks EOF, clamps that value to `bytes_per_row-i`, and then writes the bounded count. [Debian records GraphicsMagick as fixed](https://security-tracker.debian.org/tracker/CVE-2007-0770). |

The installed ImageMagick version reports source commit `acaf7e418d4ab0386e165251b7ef014a7959a205`; its [exact source](https://github.com/ImageMagick/ImageMagick/blob/acaf7e418d4ab0386e165251b7ef014a7959a205/MagickCore/profile.c) also contains the guard.

The GraphicsMagick archive was downloaded from the [official release distribution](https://downloads.sourceforge.net/project/graphicsmagick/graphicsmagick/1.3.49/GraphicsMagick-1.3.49.tar.xz) and checked against Homebrew's published SHA256: `7efa070dc31116b4315061b39f84bc7181e8b060bf61214ec9af851131af9c81`. Installed binary version output and Homebrew installation receipts were checked in an isolated guest. Both newly scoped fixes were checked in those release sources; reverting the SUN exception class or the PALM single-read guard makes the source probe fail. The PALM source came from that checksum-verified archive. This adds source applicability evidence, not a new native image build or proof against unrelated image-format bugs. These are version/source-based dispositions, not a general exemption for either project or for Homebrew packages.

Rules require the exact package name, version, `homebrew` package type and Cellar formula-record location. A new version requires a new assessment. Other findings on the same packages remain blocking unless explicitly listed as [owner-accepted legacy-runtime risks](../security-exceptions.md), including findings without a known fix. No runtime feature or exploitability assumption is used to waive a broader package range.

Run `uv run python scripts/check-macos-security.py` with Grype installed. The actual scanner must first report all disposition fixtures, then move only the accepted identities to `ignoredMatches`. Adjacent names, versions, package types and locations must retain their original findings, and High/Critical must still return exit 2.

## Withdrawn Apple OpenLDAP and httpd absence dispositions

On 2026-11-11, review withdrew 25 OpenLDAP server-only dispositions and 24 httpd module-specific dispositions. The earlier assessment treated the scanner's `ldapsearch` record as evidence that no `slapd` server existed, and default `httpd -M` output as evidence that module code was absent. Neither observation proves installed-code absence. Apple also ships loadable modules under `/usr/libexec/apache2`; an actual configuration-only probe loaded `proxy_module (shared)` with `LoadModule`, without starting a server. A later isolated macOS 26.7.3 guest probe confirmed Apple's `/usr/libexec/slapd` and the installed httpd module files. Their presence does not establish individual CVE applicability or supplier fixes.

Those 49 advisories were later recorded as owner-approved exact-version exceptions because Apple-managed httpd and OpenLDAP cannot be replaced by the image recipe. No modules or servers are removed, and no default configuration is changed. The native verifier no longer treats default module loading as a security predicate. The actual-scanner regression retains both original package fixtures and checks that only the exact package, version, type, location and advisory identities move to `ignoredMatches`. The regression in `tests/test_apple_httpd_dispositions.py` prevents widening these rules to other versions or paths.

Only Windows-specific [CVE-2026-59685](https://www.cve.org/CVERecord?id=CVE-2026-59685) is treated as inapplicable at the exact Apple `httpd` 2.4.67 binary at `usr/sbin/httpd`. The remaining Apple httpd and OpenLDAP findings are exact owner-approved retention exceptions with residual risk. This records the owner decision to keep the Apple-managed programs while the supplier versions remain outside the image's replacement boundary.

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

AWS CLI 2.39.6 and 2.40.0 require urllib3 `>=1.25.4,<=2.6.3`. The factory preserves that requirement and the official CLI release, backporting supplier security fixes into `2.6.3+melten.9ff67c841cad`. A PEP 440 local version satisfies the existing upper bound. This compatibility backport is tracked in [#8](https://github.com/melten-ai/runner-images/issues/8); it is skipped once the vendor installs a supported upstream version with these fixes. The build identity derives from the source archive, patch checksums and verified resulting modules in [refresh-aws-cli.py](image/refresh-aws-cli.py).

Four advisories are scoped separately to that exact local Python distribution at the AWS CLI 2.39.6 and 2.40.0 / Python 3.15 Cellar metadata locations. The 2.40.0 image was rebuilt and its source hashes, dependency check, CLI transfer/deletion and independent receipt verification passed again after a cold boot; other CLI versions remain blocking:

- [GHSA-mf9v-mfxr-j63j](https://github.com/advisories/GHSA-mf9v-mfxr-j63j): bounded decompression during repeated streaming reads and draining.
- [GHSA-qccp-gfcp-xxvc](https://github.com/advisories/GHSA-qccp-gfcp-xxvc): stripping sensitive headers from cross-origin low-level proxy redirects.
- [GHSA-vxq7-64xx-v4gw](https://github.com/advisories/GHSA-vxq7-64xx-v4gw): bounded chunk-size and trailer-line reads.
- [GHSA-8988-9cw3-xx77](https://github.com/advisories/GHSA-8988-9cw3-xx77): separation of proxy and target TLS settings.

Source and patch downloads require their recorded SHA256. Patching is isolated from any enclosing Homebrew Git checkout; exact resulting module hashes must match the tested backport before wheel construction and again after installation. The sdist's cached core metadata is discarded so the build receives its distinct local identity. A wheel missing that identity is rejected. Other AWS dependencies are preserved, and `pip check` runs against the actual vendor interpreter with the candidate package tree.

Before and after activation, the actual CLI writes an object to an isolated local HTTP fixture, downloads matching bytes, deletes it and observes the missing object. Only synthetic credentials and the fixed test bucket/key are accepted. This tests the HTTP transport and material file lifecycle; the fixture is not an AWS permission or SigV4 emulator and does not establish live cloud authorization. Activation or receipt-write failure restores the original package tree. Replay verifies the existing source hashes and identity without rebuilding.

The receipt records source inputs, wheel SHA256 and installed Python-file hashes. The factory independently verifies it before SBOM collection and includes the resulting report in qualification-hashed native-check evidence. The scanner retains the four source-fixed dispositions with reasons. Ordinary unpatched 2.6.3, other names, versions and locations retain their existing findings. Installed upstream urllib3 2.8.0 or newer is verified without applying this backport or these local-build exceptions.
