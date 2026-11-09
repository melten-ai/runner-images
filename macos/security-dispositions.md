# Source-verified Mac dispositions

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
