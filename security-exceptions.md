# Accepted legacy-runtime security exceptions

These are owner-accepted risks, not fixes or claims that the findings are inapplicable. Packages stay in the native and SPDX SBOMs. Grype records the finding and reason in `ignoredMatches`; all other High/Critical findings continue to block publication. A newer copy elsewhere on disk does not repair the retained copy.

When multiple versions coexist, record the exact old version, installed consumer, reason for retaining it and residual risk before adding a scoped exception. Do not exclude every older version automatically. Remove superseded copies only when their consumers and the image contract permit removal. Reassess these exceptions when the supplier or installed version changes.

## Official runner Node 20

Owner accepted on 2026-11-11: retain the official [GitHub Actions runner v2.338.2](https://github.com/actions/runner/releases/tag/v2.338.2) Node 20.20.2 binaries alongside its Node 24 runtime to preserve action compatibility and official runtime selection. Refreshing npm is separate and does not fix Node itself. Only the eight observed advisories below are accepted at the exact `binary` package named `node`, version `20.20.2`:

| Profile | Accepted SBOM location |
| --- | --- |
| Mac | `Users/admin/actions-runner/externals/node20/bin/node` |
| Cloud Linux | `opt/actions-runner/externals/node20/bin/node` |
| Cloud Linux Alpine runtime | `opt/actions-runner/externals/node20_alpine/bin/node` |

| Advisory | Observed severity |
| --- | --- |
| [CVE-2026-111497](https://nvd.nist.gov/vuln/detail/CVE-2026-111497) | Critical |
| [CVE-2026-56848](https://nvd.nist.gov/vuln/detail/CVE-2026-56848) | High |
| [CVE-2026-48937](https://nvd.nist.gov/vuln/detail/CVE-2026-48937) | High |
| [CVE-2026-56846](https://nvd.nist.gov/vuln/detail/CVE-2026-56846) | High |
| [CVE-2026-105482](https://nvd.nist.gov/vuln/detail/CVE-2026-105482) | High |
| [CVE-2026-106069](https://nvd.nist.gov/vuln/detail/CVE-2026-106069) | High |
| [CVE-2026-48617](https://nvd.nist.gov/vuln/detail/CVE-2026-48617) | High |
| [CVE-2026-58043](https://nvd.nist.gov/vuln/detail/CVE-2026-58043) | High |

Residual risk: actions executed with this runtime can still exercise vulnerable functionality. In particular, the [Node supplier advisory](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases) describes memory corruption, disclosure or code execution when processing attacker-controlled Brotli data. Coexisting Node 24 does not mitigate code that actually runs under Node 20. This exception does not certify workloads as safe.

The policies are [macos/grype.json](macos/grype.json) and [cloud/grype.json](cloud/grype.json). Other versions, paths, package identities and new advisories remain blocking. `uv run python scripts/check-runner-security.py` exercises both policies with the real scanner, including adjacent identities and an unchanged High/Critical failure exit. Policy changes participate in image rebuild fingerprints. Reassess or retire the exceptions when an official runner removes or replaces Node 20, or when the accepted-risk decision changes.

## Coexisting Apple Python and system curl

Owner accepted on 2026-11-11 under the coexisting-version decision. Apple Python 3.9.6 is retained in the exact Xcode 27.2 and Command Line Tools app-bundle locations documented in [Mac dispositions](macos/security-dispositions.md), alongside the maintained Homebrew Python runtimes. Apple curl 8.7.1 at `usr/bin/curl` is retained alongside Homebrew curl 8.23.0. These supplier-managed paths preserve existing tooling consumers. They can still execute vulnerable functionality; another installed version does not redirect every consumer or repair these copies. No program is deleted, no PATH is changed, and no fixed or non-applicable status is claimed.

Only the following observed High/Critical advisories are additionally accepted, with exact package name, version, type and location guards in `macos/grype.json`. Latest Homebrew copies, neighboring locations, new advisories and other packages remain blocking. Reassess when the OS/Xcode versions, managed paths or supplier runtime versions change.

| Retained program | Accepted advisories |
| --- | --- |
| Python | [CVE-2015-20107](https://nvd.nist.gov/vuln/detail/CVE-2015-20107), [CVE-2018-25032](https://nvd.nist.gov/vuln/detail/CVE-2018-25032), [CVE-2020-10735](https://nvd.nist.gov/vuln/detail/CVE-2020-10735), [CVE-2021-28861](https://nvd.nist.gov/vuln/detail/CVE-2021-28861), [CVE-2022-37454](https://nvd.nist.gov/vuln/detail/CVE-2022-37454), [CVE-2022-45061](https://nvd.nist.gov/vuln/detail/CVE-2022-45061), [CVE-2023-24329](https://nvd.nist.gov/vuln/detail/CVE-2023-24329), [CVE-2023-36632](https://nvd.nist.gov/vuln/detail/CVE-2023-36632), [CVE-2023-6597](https://nvd.nist.gov/vuln/detail/CVE-2023-6597), [CVE-2024-0397](https://nvd.nist.gov/vuln/detail/CVE-2024-0397), [CVE-2024-4032](https://nvd.nist.gov/vuln/detail/CVE-2024-4032), [CVE-2024-6232](https://nvd.nist.gov/vuln/detail/CVE-2024-6232), [CVE-2024-7592](https://nvd.nist.gov/vuln/detail/CVE-2024-7592), [CVE-2024-8088](https://nvd.nist.gov/vuln/detail/CVE-2024-8088), [CVE-2024-9287](https://nvd.nist.gov/vuln/detail/CVE-2024-9287), [CVE-2025-13836](https://nvd.nist.gov/vuln/detail/CVE-2025-13836), [CVE-2025-8194](https://nvd.nist.gov/vuln/detail/CVE-2025-8194), [CVE-2026-115557](https://nvd.nist.gov/vuln/detail/CVE-2026-115557), [CVE-2026-11972](https://nvd.nist.gov/vuln/detail/CVE-2026-11972), [CVE-2026-15308](https://nvd.nist.gov/vuln/detail/CVE-2026-15308), [CVE-2026-19445](https://nvd.nist.gov/vuln/detail/CVE-2026-19445), [CVE-2026-19553](https://nvd.nist.gov/vuln/detail/CVE-2026-19553), [CVE-2026-3644](https://nvd.nist.gov/vuln/detail/CVE-2026-3644), [CVE-2026-4224](https://nvd.nist.gov/vuln/detail/CVE-2026-4224), [CVE-2026-4786](https://nvd.nist.gov/vuln/detail/CVE-2026-4786), [CVE-2026-6100](https://nvd.nist.gov/vuln/detail/CVE-2026-6100), [CVE-2026-7210](https://nvd.nist.gov/vuln/detail/CVE-2026-7210), [CVE-2026-9669](https://nvd.nist.gov/vuln/detail/CVE-2026-9669) |
| curl | [CVE-2024-6197](https://nvd.nist.gov/vuln/detail/CVE-2024-6197), [CVE-2025-9086](https://nvd.nist.gov/vuln/detail/CVE-2025-9086), [CVE-2026-10536](https://nvd.nist.gov/vuln/detail/CVE-2026-10536), [CVE-2026-11856](https://nvd.nist.gov/vuln/detail/CVE-2026-11856), [CVE-2026-13608](https://nvd.nist.gov/vuln/detail/CVE-2026-13608), [CVE-2026-18924](https://nvd.nist.gov/vuln/detail/CVE-2026-18924), [CVE-2026-19931](https://nvd.nist.gov/vuln/detail/CVE-2026-19931), [CVE-2026-5773](https://nvd.nist.gov/vuln/detail/CVE-2026-5773), [CVE-2026-6276](https://nvd.nist.gov/vuln/detail/CVE-2026-6276), [CVE-2026-80230](https://nvd.nist.gov/vuln/detail/CVE-2026-80230), [CVE-2026-80231](https://nvd.nist.gov/vuln/detail/CVE-2026-80231), [CVE-2026-82209](https://nvd.nist.gov/vuln/detail/CVE-2026-82209), [CVE-2026-8286](https://nvd.nist.gov/vuln/detail/CVE-2026-8286), [CVE-2026-86409](https://nvd.nist.gov/vuln/detail/CVE-2026-86409), [CVE-2026-86414](https://nvd.nist.gov/vuln/detail/CVE-2026-86414), [CVE-2026-87863](https://nvd.nist.gov/vuln/detail/CVE-2026-87863), [CVE-2026-87864](https://nvd.nist.gov/vuln/detail/CVE-2026-87864), [CVE-2026-8924](https://nvd.nist.gov/vuln/detail/CVE-2026-8924), [CVE-2026-8927](https://nvd.nist.gov/vuln/detail/CVE-2026-8927), [CVE-2026-8932](https://nvd.nist.gov/vuln/detail/CVE-2026-8932), [CVE-2026-92386](https://nvd.nist.gov/vuln/detail/CVE-2026-92386), [CVE-2026-92388](https://nvd.nist.gov/vuln/detail/CVE-2026-92388), [CVE-2026-92389](https://nvd.nist.gov/vuln/detail/CVE-2026-92389), [CVE-2026-92390](https://nvd.nist.gov/vuln/detail/CVE-2026-92390), [CVE-2026-92391](https://nvd.nist.gov/vuln/detail/CVE-2026-92391), [CVE-2026-94266](https://nvd.nist.gov/vuln/detail/CVE-2026-94266) |

The Mac actual-scanner boundary regression covers these rules, both Python locations, adjacent identities and the unchanged failure exit for remaining High/Critical findings. Existing source-verified and runtime-predicate dispositions remain separately documented; risk acceptance does not replace those checks.
