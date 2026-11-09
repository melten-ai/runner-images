# Runner images

GitHub Actions runner images for Melten.

## What is measured

- Executable names reachable on `PATH`.
- Tool-cache families with a setup-action `.complete` marker.
- Image environment variable names, excluding runner/job/session metadata. Environment values, home directories, hostnames and full executable paths are not recorded.
- OS, architecture and optional `ImageOS`/`ImageVersion` provenance. Only equal OS/architecture pairs can be compared.

Comparison fails for unexpected missing capabilities and stale allowlist entries. Extra capabilities are reported without failing. An executable name proves discoverability; product tests and version checks still belong to the image consumer.

## Run locally

Python 3.11 or newer is the only runtime dependency. Run the same collector on each machine; it needs neither cloud credentials nor root access.

```sh
python3 scripts/inventory.py > candidate.json
python3 scripts/compare.py reference.json candidate.json
python3 scripts/compare.py reference.json candidate.json --allowlist accepted-missing.txt
```

Output is JSON. Comparison exits 0 when compatible, 1 for capability differences, and 2 for invalid/unreadable input or mismatched platforms. The allowlist is a reviewed text file, one record per line, such as `cmd example-tool` or `cache ExampleRuntime`. Missing and stale exceptions are checked in both directions. Keep candidate inventories and company-specific exception policies in their owning repositories.

## GitHub Actions

The [Hosted reference inventories](https://github.com/melten-ai/runner-images/actions/workflows/reference.yml) workflow captures standard `ubuntu-24.04` and `macos-26` images before setup actions change their toolchain. It runs daily at **09:30 KST (00:30 UTC)**, on pushes to main, on pull requests, and on manual dispatch.

**Git is the durable reference:** [`snapshots/github-hosted/`](snapshots/github-hosted/) contains the current measured JSON for each image. Changed commands, caches, environment names or image versions produce a commit; identical observations produce no commit. Git history preserves previous observations and readable diffs. Observation timestamps and run counters are not inserted into snapshots, so reruns do not create artificial changes. Initial snapshots were captured by [the verified hosted run](https://github.com/melten-ai/runner-images/actions/runs/42367660196).

Artifacts only transfer the two matrix jobs’ results to the Git recording job. That job validates the entire matrix, updates only the snapshot directory, and commits with the workflow token. Public PRs cannot write snapshots; only trusted main runs can. Publishing is serialized, starts from current main, and never force-pushes over a concurrent change. A failed/incomplete observation preserves the previous references. Scheduled recording starts once this workflow is merged to the default branch.

The root composite Action collects a candidate:

```yaml
- uses: melten-ai/runner-images@VERIFIED_COMMIT
  with:
    output: candidate.json
```

Compare after downloading the matching reference artifact:

```yaml
- uses: melten-ai/runner-images/compare@VERIFIED_COMMIT
  with:
    reference: reference/inventory.json
    candidate: candidate.json
    allowlist: accepted-missing.txt
```

Replace `VERIFIED_COMMIT` with the reviewed code revision. Consumers fetch the public repository’s main branch, read `snapshots/github-hosted/macos-26.json` (or `ubuntu-24.04.json`), and retain that reference commit with their comparison evidence. Reading versioned references needs no Actions-artifact permission or expiring artifact URL. During initial adoption before the public PR is merged, the same verified PR commit also contains the seeded snapshots.

Candidate machines run in their owning private workflows. This repository does not accept public PR code on a private self-hosted runner and never uploads private candidate inventories. Reference collection uses standard GitHub-hosted public-repository runners; cloud VM costs and larger-runner billing are separate.

## Build a Tart image

On an Apple Silicon Mac with Tart, curl, jq, authenticated GitHub CLI and Python 3.11+, run:

```sh
bash macos/build-image.sh example-runner
bash macos/build-image.sh example-android ghcr.io/cirruslabs/macos-tahoe-xcode:latest xcode-android
```

The arguments are destination image, base image (a local Tart image or OCI reference), profile (`xcode` by default, or `xcode-android`), and guest runner directory (`/Users/admin/actions-runner` by default). The Cirrus base supplies macOS and Xcode.

Before provisioning tools, the build installs the newest available macOS update within the base image’s major version; it never selects a major upgrade. Only a generated temporary VirtualMac build guest can be updated. For the known Cirrus disk layout missing its system recovery partition, the updater verifies disk identity and resize limits before creating that partition inside the disposable guest. Unknown layouts fail closed. Completion requires both the target OS version and a changed guest boot time, and `os-update.txt` records the verified version. Unattended updates authenticate with the [Cirrus template’s published default guest owner](https://github.com/cirruslabs/macos-image-templates/blob/main/templates/xcode.pkr.hcl) (`admin`/`admin`) through standard input. The guest account is checked before disk changes; custom credentials fail explicitly. Host account credentials are not used.

The scripts provision GitHub’s macOS toolset, Ruby gems, Rust components, Python/Node/Go caches and Temurin JDKs; the Android profile adds SDK command-line tools, platform-tools and the toolset’s minimum platform/build-tools packages. This is a concrete supported tool subset, not an assertion that every hosted software family is installed.

Build from a clean committed checkout so every verification is tied to an exact source revision. The optional fifth/sixth arguments select a reference JSON and reviewed missing-capability list; defaults are the versioned macOS reference and `macos/accepted-missing/PROFILE.txt`. Those lists explicitly describe the supported profiles’ intentional omissions.

The builder selects an active physical bridge even when a VPN owns the default route. It refuses an existing destination and a fully occupied two-VM host, builds an isolated temporary clone, installs the checksum-verified GitHub runner without registration credentials, checks the job toolchain, captures the native check output, resolved toolset and inventory, stops the clone, compares against the reference and hashes the base/final VM files. It publishes under the new local name only after comparison passes, rechecks the renamed image’s hashes and then issues `artifacts/tart/IMAGE/qualification.json`. Failed comparison or identity checks issue no passing receipt. Failure stops/removes only its temporary clone. Production fleet images and runner configuration are not switched by this script. Keep private image identifiers, credentials and company configuration with the fleet owner.

The Go cache follows the supported release lines returned by [Go's official downloads API](https://go.dev/dl/?mode=json), using their latest patches rather than retaining unsupported versions from GitHub's toolset. All selected ARM compilers must execute at the exact resolved version before superseded or unsupported ARM cache entries are retired; other architectures and inherited symlinks are preserved. The effective toolset records the resolved Go policy, while the guest retains the original upstream toolset separately. Projects that explicitly request an older Go version can still download it through `setup-go`; that version is not preinstalled in this image. The obsolete `GOROOT_1_24_ARM64` and `GOROOT_1_25_ARM64` environment names are explicit comparison omissions in both profiles. This changes preinstallation, not a security-scan exception.

Python cache entries use the current Homebrew security patch of each requested minor branch, including security-only branches whose upstream prebuilt archive is older. Cache directory versions come from the running interpreter; Python and pip links are verified before completion markers are written. Existing shared Python.org framework paths are connected to the verified same-minor Homebrew framework, with restoration if the consumer probe fails. The VM retains the matching Homebrew installation. Python runs natively as ARM64; the five Intel-only `python3*-intel64` launchers in the hosted inventory are intentional omissions in both profiles. `2to3` remains available through the patched Python 3.12 installation, with a real Python 2-to-3 conversion checked before qualification. Native verification also compiles and runs C/C++, Go, Rust, Java and Swift programs, builds CMake/Ninja and offline Cargo projects, and exercises Python venv/SSL/SQLite, Node crypto and Ruby OpenSSL/JSON before collecting the SBOM. Only superseded arm64 patches of the same minor are retired; other minor branches and architectures remain. The build also upgrades inherited Homebrew core formulas and cleans obsolete Cellar versions before measuring the image. Required third-party formulas are authorized individually; unrelated inherited taps are not trusted or upgraded. The host stages the official Azure CLI cask through the authenticated GitHub asset API, requiring agreement between Homebrew’s checksum, GitHub’s asset digest and the bytes transferred to the guest; host credentials stay on the host. Native verification records the macOS product and build versions.

Guest scripts live in [`macos/image/`](macos/image/). Reuse a pinned base digest when an exact base is required; the resolved toolset is retained inside the guest under `~/.runner-image/`. Xcode comes from the selected base image; this script does not redistribute a host Xcode installation or export simulator runtimes.

## One command from the rebuild issue

The daily reference job records real hosted-image changes at 09:30 KST. [Reconcile image rebuild issues](.github/workflows/rebuild-issues.yml) compares the current macOS reference/build inputs with released images. It maintains **one reusable bot-owned issue per profile** (`xcode`, `xcode-android`), including a command already filled in for that profile. Copy that command into a terminal on your Apple Silicon Mac; there are no workflow inputs, image names, version numbers, tokens or follow-up release commands to enter.

The command downloads the public `macos/rebuild.sh` bootstrap through your existing `gh` login. It installs missing Tart/jq/Grype/Python tools through Homebrew, prepares its own checkout under `${XDG_STATE_HOME:-~/.local/state}/runner-images/melten-ai/PROFILE/source`, and runs the repository's local build command. Your ordinary working tree and running fleet configuration are untouched. `gh`, an authenticated GitHub account with repository/workflow/package write access, and Homebrew are prerequisites. These are the account's existing permissions; no physical Actions runner registration or new runner group is required.

The local command waits for a free VM slot, builds a uniquely named image, generates native Syft and SPDX guest SBOMs from the same scan, checks the native toolchain and capability comparison, stops the VM and checks its bytes. Grype scans the native Syft SBOM with installed-package metadata and Go symbols; high/critical findings block publication. It then uploads that exact image to GHCR, reads back its immutable digest and qualification labels, commits verified evidence to main using the existing GitHub login, and starts the main-only [Release verified Tart image](.github/workflows/release-tart.yml) workflow. The terminal waits for that workflow to finish; you do not have to visit Actions or manually close the issue.

The host resolves runner and scanner release metadata through the authenticated GitHub CLI before starting a VM; only public asset metadata reaches the guest. The guest downloads the latest official Syft archive, verifies its SHA-256 and runs it with noninteractive root access inside the guest so protected installed system tools are readable. The temporary scanner directory is removed after collection. This recipe does not install the build-only scanner as a Homebrew formula. The official runner's Node binaries are preserved; their bundled npm trees are replaced with integrity-verified stable npm 11 (Node 20) and npm 12 (Node 24), with a runtime version check before qualification. Unsupported npm major releases fail the build for review.

The local qualification and hosted release both compare package names, versions and counts across the native and SPDX SBOMs, excluding only the directory source entry represented by SPDX. This catches inconsistent scan and attestation inventories while allowing document IDs and timestamps to differ.

GitHub independently validates the evidence against its original source revision on main, repeats the comparison and GHCR read-back, scans the native Syft SBOM again, and signs the SBOM and local-build validation evidence. `oneup` computes the next CalVer version with a separate `tart-PROFILE@YY.MM.MICRO` tag sequence. The workflow uploads the evidence as release assets, verifies their SHA256 digests, publishes the GitHub Release and records `release.json` in Git. A draft is only an intermediate upload stage; a completed run leaves a published release. Interrupted publication repairs that same release instead of creating another version. The signed custom predicate explicitly identifies the builder as a **local Mac** and attests GitHub's independent validation; it does not claim that GitHub built the VM.

Only then does the serialized issue reconciler close the matching reminder. An uploaded candidate without a completed release cannot close an issue. If the reference or recipe changed while the build ran, the older release is preserved and the current request stays open. Repeated detection makes no comments or duplicate issues. Later changes reopen the same issue; unrelated issues are preserved. The reconciler reads current main after trusted reference/release runs, on main pushes and daily at 09:45 KST. The completion workflow handles workflow-token commits explicitly rather than relying on push events that GitHub suppresses.

The local journal lives in the isolated checkout's ignored `artifacts/` directory. Run the **same issue command** after a failure or interruption: verified images/uploads are reused, failed generated records are preserved under `artifacts/recovery/`, and an existing release run is reconnected or rerun. A second concurrent local invocation cannot build another image for that profile. Repeating a completed command when current inputs are already released is a no-op. Failed builds retain diagnostics under their own unique names and preserve prior images.

## Image and release evidence

Git is the durable store: `qualifications/tart/PROFILE/RECEIPT_SHA256/` contains the qualification receipt, inventory, upstream toolset, native check output, guest SBOM, registry `publication.json` and completed `release.json`. Source/reference revisions, input hashes and stopped VM file hashes bind that record to the actual qualified candidate. Release assets add the vulnerability report and signed validation/SBOM bundles. Fixtures are never published as real build evidence.

The VM itself is stored as `ghcr.io/melten-ai/runner-images/macos-26-PROFILE:macos-26-PROFILE-local-BUILD_ID`. Use the immutable `@sha256:…` reference shown in the release notes to clone it with Tart. GitHub Releases contain the version, pull command and verification evidence, rather than another multi-gigabyte copy of the VM. Package visibility follows GHCR permissions and is not changed automatically; a public release page does not imply anonymous registry access. Fleet adoption remains the consumer's decision.

The SBOM describes software discoverable by Syft in the fresh guest, excluding protected/system, volatile and user Library trees. Native product tests, complete hosted-image equivalence, proprietary installer licensing and public package-visibility changes are not established by a capability comparison or SBOM. This PR implements the command and workflow; the first real local build/upload/release remains to be run after merge. Public PR checks use hosted runners and controlled external-boundary fixtures.

## Automatic GCP and Azure images

[Release cloud runner images](.github/workflows/release-cloud.yml) owns the entire cloud path. It resolves the current Ubuntu 24.04 x86-64 base on each provider, resolves official toolchain versions once, hashes their download URLs/checksums with that concrete base ID, the versioned hosted reference, destination and cloud recipe, and skips unchanged released inputs. It runs once after successful trusted hosted-reference collection, which already runs on main pushes and daily at **09:30 KST**, including workflow-token snapshot commits. Manual cloud dispatch remains available. Cloud automatic publication is explicitly requested for this repository; the macOS path keeps its local-command/manual release contract. PRs cannot authenticate to clouds, build images or publish releases.

Packer uses one shared Linux provision/verification recipe. It installs the basic C/C++/CMake, Git, Python, Node, Go, Java, Rust and Docker tools plus a checksum-verified unregistered GitHub runner. It executes native compile/runtime smoke checks and Docker daemon checks inside the provisioning VM, captures actual capabilities and a guest SBOM, downloads evidence, removes provisioning SSH authorization and temporary installers, and generalizes the image. The VM has no repository registration credentials; GCP has no default VM service account. GCP SSH ingress is restricted to the Actions builder's address on the configured dedicated network; Azure restricts temporary ingress to that address too.

The cloud guest installs current official Node LTS, stable Go/Rust and Git LFS archives with verified SHA-256 checksums, plus Docker's signed upstream packages. Buildx resolves its current official security release independently of apt, verifies the GitHub release SHA-256 digest and replaces the older apt plugin, so stale plugin binaries do not remain in the image. npm is resolved independently from the registry and verified against its SHA-512 integrity, so an older npm bundled with Node does not freeze its dependencies. The runner's Node 24 npm is refreshed to the current stable npm; its Node 20 npm stays on the latest stable npm 11 release, because npm 12 requires Node 22 or later. The official runner Node binaries and runtime selection remain intact. npm trees are replaced completely so removed vulnerable dependencies cannot remain on disk. Download URLs, checksums and selected toolchain versions are included in the evidence. These versions are resolved before the build/no-op decision and the same manifest is installed in the guest; changed tool releases trigger a build even if the OS base and recipe are unchanged. Runner npm replacement preserves existing ownership so the runner account can remove old versions. Apt downloads and disabled Snap revisions are removed before scanning; installed packages remain in scope. The checksum-verified scanner runs from a temporary build directory and is removed before image capture. The root filesystem is cataloged with Syft's `image` preset: installed packages and binaries remain in scope, while source-only dependency declarations and bundled upstream CI workflows are not presented as installed software. Grype consumes the native Syft JSON with distro/source-package metadata, file locations and Go function symbols. SPDX remains available for distribution and SBOM attestations. High/critical findings still block publication; a richer SBOM is not a security exception.

GCP stores a new uniquely named Compute Engine image. Azure stores a CalVer image version in the configured Compute Gallery. Independent cloud read-back checks readiness, build ownership and source/recipe tags; the Packer manifest must identify the expected artifact. Grype scanning and signed image-metadata/SBOM attestations precede GitHub Release publication. The release binds the provider's concrete image ID, source/base revisions, input fingerprint and checksummed evidence; `cloud-gcp@YY.MM.MICRO` and `cloud-azure@YY.MM.MICRO` have separate oneup version sequences. Signed subjects hash the image metadata, not an unmeasured cloud disk's bytes.

Cloud planning requires `oneup` on `PATH`. Hosted builds install the current stable oneup and Grype release archives into a temporary tools directory and verify their official SHA-256 digests. Authenticated hosts download through the GitHub asset API; guest installers use public download URLs without receiving the host token. Both cloud and Tart release workflows run Grype with `--fail-on high` and retain JSON findings; scanner installation or integrity failures also stop publication.

This initial cloud profile is **provisioning-smoke qualified**, not full GitHub-hosted parity or a fresh-image boot/real Actions-job test. The hosted comparison report is attached even when it reports missing capabilities; its differences are informational for this base profile. Release notes explicitly state those boundaries. Native smoke, evidence integrity, cloud identity/readiness, vulnerability gates and release asset checks are mandatory. Fresh-image boot and real Actions-job testing are a separate next stage.

There are **no cloud rebuild issues**. Provider builds are serialized independently. Identical successful runs do not create another VM or release. A published release whose final Git commit failed is recovered from its checksummed image receipt and cloud read-back. Successful Git records live under `qualifications/cloud/PROVIDER/INPUT_SHA256/`; artifacts retain provisioning/release/cleanup diagnostics. Cloud changes do not request a macOS rebuild.

Each job always attempts to remove its owned temporary VM/network/disk resources and any image that never reached a published release. Packer cleanup is reinforced with explicit provider cleanup. Azure uses a dedicated precreated build resource group, separate from the gallery group, so the identity need not create arbitrary subscription resource groups. Cleanup filters the exact build ownership tag; it refuses foreign resources and reports incomplete deletion as a failed job. Images referenced by a published release are preserved even if Git recording fails. Later scheduled no-op runs also retry temporary cleanup for the released build. Images from previous releases are retained; retaining those images has storage cost. As with any runner-local finalizer, a lost runner can prevent its cleanup step from executing; diagnostics and resource ownership tags identify that recovery work.

### Cloud setup

The cloud destinations and trust identities are deployment configuration, never secrets baked into source or images. GitHub authenticates with OIDC scoped to immutable repository/owner IDs, `refs/heads/main`, and the reusable `cloud-image.yml` job. This avoids reliance on the mutable/default OIDC subject format. Use a dedicated GCP network without a broad preexisting SSH allow rule. Configure these repository variables before merging/enabling automatic cloud builds:

| Provider | Repository variables |
| --- | --- |
| GCP authentication | `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT` |
| GCP destination | `GCP_PROJECT`, `GCP_ZONE`, `GCP_NETWORK`, `GCP_MACHINE_TYPE` |
| Azure authentication | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` |
| Azure destination | `AZURE_LOCATION`, `AZURE_VM_SIZE`, `AZURE_BUILD_RESOURCE_GROUP`, `AZURE_RESOURCE_GROUP`, `AZURE_GALLERY`, `AZURE_IMAGE_DEFINITION` |

Enable Compute Engine on the chosen GCP project and grant the build identity the instance/disk/image and dedicated-network firewall operations used by the recipe. Do not grant a VM service identity. On Azure, precreate the build/gallery groups and a generalized Linux x64 Generation 2 Ubuntu 24.04 gallery definition; scope the build identity to those groups plus required marketplace/location reads. Grant GitHub OIDC federation only for this main workflow, not arbitrary public branches. No long-lived cloud key or GitHub Secret is needed. `cloud/configure-auth.py` provisions dedicated identities from a private configuration file using an existing administrator login; it grants only the image/VM operation role on GCP and scoped build/image-definition roles plus catalog reads on Azure. It never creates cloud access keys or client passwords. Destination identities are configured outside source through repository variables; only a live workflow run establishes cloud-build evidence. Release receipts omit runtime authentication/network configuration.

To validate recipes locally without credentials/resources:

```sh
python3 scripts/check-cloud.py
python3 scripts/check-sbom.py /path/to/syft
```

This uses Packer 1.16.2 with Google Compute 1.2.9 and Azure 2.6.5 plugins. `packer init` installs the declared compatible plugin versions. The public PR gate validates both real plugin schemas using only synthetic destination values in `cloud/validation.pkrvars.json`. The same Python `scripts/cloud-image.py` phases run in Actions or locally with explicit destination variables and an authenticated cloud CLI; authenticated live execution incurs cloud costs.

## Development and verification

```sh
uv sync --frozen
uv run python scripts/check.py
uv run python scripts/test.py
uv run ruff format .
shfmt -w scripts macos
```

Install ShellCheck and shfmt with your system package manager. The gate runs formatting, Python/shell lint, strict Python types and behavioral tests with branch coverage (minimum 80%). Tests exercise real executable files/symlinks, cache completion, secret-value omission, incompatible platforms, invalid snapshots and stale allowlists; Git recording is exercised against a real local bare remote, including repeat no-op and dirty-worktree refusal. Tart lifecycle tests cover successful publication, provisioning/verification/rename failures, cleanup and preservation of unrelated images through a controlled command boundary. A local real-VM build is a separate integration check, not established by those fixtures.

| Tooling | Version at selection (2026-11-07) | Registry release | Reason |
| --- | --- | --- | --- |
| Ruff | 0.17.4 | 2026-11-05 | Native formatter and linter; selected over formatter-only Black 26.10.1 (2026-10-10) |
| pytest / pytest-cov | 9.2.0 / 7.1.0 | 2026-10-27 / 2026-03-21 | Behavioral tests and branch coverage |
| Pyright | 1.1.414 | 2026-09-10 | Strict typed collection/comparison boundary |
| shfmt / ShellCheck | 3.14.1 / 0.11.0 locally | Distribution packages in CI | Maintained native shell formatter/linter; avoids an interpreter-based formatter or bashate lint pipeline |

`setup-uv` uses its current full release ref because upstream does not publish a `v10` alias. Development dependencies use compatible ranges and `uv.lock`; runtime collection/comparison uses only the standard library. There is no installed CLI/package release or fleet deployment; consume a reviewed Git revision. Fresh-image cloud boot/Actions validation, Windows, version-level probes and official upstream test integration remain future work.

## Related projects

[actions/runner-images](https://github.com/actions/runner-images) is the official upstream source for toolsets, provisioning, software reports and runtime tests. [runner-drift](https://github.com/Booyaka101/runner-drift) monitors selected tool-version changes and migrations. [Mactions](https://github.com/Kyter-com/Mactions) manages ephemeral runners and documents hosted differences. This repository compares observations from both machines rather than treating a published manifest as proof of a candidate’s contents.

### Local consumers after macOS publication

The issue command can notify a host-local image consumer after it reads a published release receipt from `origin/main`. Register a JSON command array at `${XDG_CONFIG_HOME:-~/.config}/runner-images/melten-ai/<profile>/after-release.json`, for example `["/absolute/path/to/example-consumer"]`. The command receives the absolute qualification-bundle directory as its last argument. Arguments are passed directly, without a shell. No consumer configuration or credentials are committed here.

This callback also runs for an already published matching image, so registering later or repeating a failed adoption does not require another build. A callback failure fails the local command and leaves its journal unfinished. The public rebuild issue tracks image publication and closes after verified release publication; private consumer adoption is recorded by that consumer. Without a registered callback the command only publishes the image and never changes a fleet.
