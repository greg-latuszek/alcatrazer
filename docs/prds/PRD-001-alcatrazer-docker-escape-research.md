---
title: "PRD-001 Alcatrazer — Docker Container Escape Supporting Evidence"
purpose: "Real, verifiable Docker container escape incidents and CVEs (2021–2026), classified by root cause: Docker engine/runtime flaw vs misconfigured/curated image"
date: 2026-05-07
author: "Grzegorz Latuszek"
status: research-note
---

# Docker Container Escape Supporting Evidence (PRD-001)

This is a sibling research note to
[PRD-001-alcatrazer-threat-model-research.md](./PRD-001-alcatrazer-threat-model-research.md).

It answers the question: **Alcatrazer's current sandboxing machinery is a
curated Docker container — are there known incidents where code escaped from
Docker onto the host, and when this happens, is the weakness in the Docker
system itself or in how the image was curated/configured?**

The answer is *both*, with material evidence on each side. Each entry below is
classified with an explicit **Root cause** line:

- **Engine/runtime flaw** — bug in Docker, runc, containerd, BuildKit, the
  kernel, or a tightly coupled component (e.g. NVIDIA Container Toolkit). Even
  a perfectly curated image is vulnerable until the engine is patched.
- **Image/configuration flaw** — operator-side mistake: `--privileged`,
  mounted Docker socket, host bind mount, dangerous capabilities, exposed API
  port, or a malicious/compromised image pulled from a registry.
- **Mixed** — engine bug whose blast radius depends on operator configuration.

Scope: incidents with assigned CVEs, vendor/CERT advisories, or PoCs published
by reputable researchers (Snyk, Wiz, Sysdig, Unit 42, Trend Micro/ZDI, NCC,
academic / national labs). Time window: **2021–2026**, with two earlier
"foundational" CVEs included for reference because they still shape current
thinking.

---

## Section 1 — Docker engine / runtime / kernel CVEs (engine fault)

### 1.1 Leaky Vessels — CVE-2024-21626 (runc) + CVE-2024-23651/23652/23653 (BuildKit), January 2024
Snyk researcher Rory McNamara disclosed a four-CVE family in core container
infrastructure. The headline bug, **CVE-2024-21626** (CVSS 8.6), is an
order-of-operations breakout in `runc ≤ 1.1.11`: a leaked file descriptor
plus the `WORKDIR` directive let a freshly spawned container process land
with its working directory in the *host* filesystem namespace, giving full
host access. The three BuildKit bugs each break out at *image-build* time:
**CVE-2024-23651** is a TOCTOU race on shared cache mounts that exposes host
files to the build sandbox; **CVE-2024-23652** lets a malicious Dockerfile
delete arbitrary host files via temp-dir manipulation; **CVE-2024-23653** is
a missing privilege check in a BuildKit gRPC endpoint that grants build-time
privileged execution. All four were patched in `runc 1.1.12`,
`BuildKit 0.12.5`, Moby/Docker Engine 25.0.2 and Docker Desktop 4.27.1.
Wiz reported **80 % of cloud environments were vulnerable** at disclosure.
**Root cause: engine/runtime flaw.** Even a fully hardened image was
exploitable — the bug lived in the runtime and the build-time daemon.
Source: <https://snyk.io/blog/cve-2024-21626-runc-process-cwd-container-breakout/> ·
<https://www.docker.com/blog/docker-security-advisory-multiple-vulnerabilities-in-runc-buildkit-and-moby/> ·
<https://www.wiz.io/blog/leaky-vessels-container-escape-vulnerabilities>

### 1.2 The November 2025 runc trio — CVE-2025-31133, CVE-2025-52565, CVE-2025-52881
A second cluster of high-severity runc breakouts, disclosed by Sysdig and
patched in `runc 1.2.8 / 1.3.3 / 1.4.0-rc.3`. **CVE-2025-31133** lets the
attacker replace `/dev/null` with a symlink during container creation,
tricking runc into mounting arbitrary host paths into the container — write
access to `/proc/sys/kernel/core_pattern` is enough to escape. **CVE-2025-52565**
exploits insufficient validation of `/dev/pts/$n → /dev/console` mounts to
redirect them before security protections activate. **CVE-2025-52881** is the
most sophisticated: it bypasses LSM (AppArmor / SELinux) checks by making
`/proc/self/attr/<label>` point at a real procfs file, which is then used to
redirect writes to host targets like `/proc/sysrq-trigger` (host crash) or
`/proc/sys/kernel/core_pattern` (full breakout). Because runc is the default
runtime for Docker, Podman, containerd-via-Kubernetes, and CRI-O, "unpatched
systems face an immediate risk of container escape and host compromise, even
when kernel vulnerabilities are not present."
**Root cause: engine/runtime flaw.**
Source: <https://www.sysdig.com/blog/runc-container-escape-vulnerabilities> ·
<https://github.com/opencontainers/runc/security/advisories/GHSA-9493-h29p-rfm2> ·
<https://www.cncf.io/blog/2025/11/28/runc-container-breakout-vulnerabilities-a-technical-overview/>

### 1.3 CVE-2025-9074 — Docker Desktop unauthenticated container escape, August 2025
Disclosed by Felix Boulet. CVSS 9.3, patched in Docker Desktop 4.44.3,
affects Windows and macOS only (the Linux version uses a host-side named
pipe and is unaffected). The Docker Engine API was bound to `0.0.0.0:2375`
inside the WSL2 / Hyperkit VM, so *any* container could reach it at
`http://192.168.65.7:2375` **without authentication and without needing the
Docker socket mounted**. A single HTTP request from a container could spawn
a privileged sibling container that mounts the host's `C:\` drive — on
Windows, the PoC reads any file as administrator and overwrites a system DLL
to escalate to host administrator.
**Root cause: engine/runtime flaw** — improper bind address in Docker Desktop's
internal daemon. Defeats every operator-side hardening short of removing
Docker Desktop entirely.
Source: <https://nvd.nist.gov/vuln/detail/CVE-2025-9074> ·
<https://thehackernews.com/2025/08/docker-fixes-cve-2025-9074-critical.html> ·
<https://www.mindpatch.net/posts/docker-escape-ssrf/>

### 1.4 NVIDIAScape — CVE-2025-23266 (NVIDIA Container Toolkit), July 2025
Disclosed at Pwn2Own Berlin 2025 by Wiz and assigned by NVIDIA on 2025-07-15.
CVSS 9.0; affects NVIDIA Container Toolkit ≤ 1.17.7 and NVIDIA GPU Operator
≤ 25.3.0. The OCI `createContainer` hook inherited environment variables
from the container, including `LD_PRELOAD`. By setting `LD_PRELOAD` in their
Dockerfile and dropping a malicious shared library into the image, an
attacker has the privileged hook load and execute their code on the host
during container start. Wiz called it "incredibly easy to weaponize" and
warned the impact is infrastructure-wide on multi-tenant GPU clouds (the
exact deployment shape used by AI training/inference platforms).
**Root cause: engine/runtime flaw** in the GPU runtime adjacent to Docker.
Particularly relevant to AI tooling because GPU passthrough is the default
configuration for AI workloads on shared hardware.
Source: <https://www.wiz.io/blog/nvidia-ai-vulnerability-cve-2025-23266-nvidiascape> ·
<https://thehackernews.com/2025/07/critical-nvidia-container-toolkit-flaw.html> ·
<https://nvd.nist.gov/vuln/detail/CVE-2025-23266>

### 1.5 Pwn2Own Berlin 2025 — Docker Desktop kernel UAF escape, May 2025
Billy Jheng Bing-Jhong and Muhammad Alifa Ramdhan (STAR Labs SG) won the
Cloud/Container category by chaining a Linux kernel use-after-free into a
full Docker Desktop escape — the day-one top single payout at $60,000.
ZDI/Trend Micro published a follow-up writeup on novel Docker Desktop VM
escape techniques under WSL2.
**Root cause: kernel/runtime flaw** (kernel UAF leveraged from inside a
container). The container was a stock Docker Desktop install; no operator
misconfiguration was required.
Source: <https://starlabs.sg/achievements/p2o-berlin-2025/> ·
<https://www.securityweek.com/hackers-win-260000-on-first-day-of-pwn2own-berlin-2025/> ·
<https://www.trendmicro.com/vinfo/us/security/news/virtualization-and-cloud/cracking-the-isolation-novel-docker-desktop-vm-escape-techniques-under-wsl2>

### 1.6 CVE-2022-0185 — fsconfig kernel heap overflow exploitable from unprivileged containers, January 2022
A heap buffer overflow in the Linux kernel's `legacy_parse_param`
(`fs/fs_context.c`) reachable via the `fsconfig` syscall. Despite needing
`CAP_SYS_ADMIN`, the bug is exploitable from inside an *unprivileged* user
namespace — including default Kubernetes pods and stock Docker containers —
which is what made it a "gamechanger" for container escape per Aqua,
CrowdStrike and Sysdig. Affects vanilla kernels 5.1 → 5.16.1; the Will's
Root writeup won a $31,337 KCTF bounty by escaping Google's hardened
container.
**Root cause: kernel flaw.** Workaround was to disable unprivileged user
namespaces — i.e. degrade Docker's defense-in-depth — until the kernel
patched.
Source: <https://www.crowdstrike.com/en-us/blog/cve-2022-0185-kubernetes-container-escape-using-linux-kernel-exploit/> ·
<https://www.sysdig.com/blog/cve-2022-0185-container-escape> ·
<https://www.willsroot.io/2022/01/cve-2022-0185.html>

### 1.7 Reference — CVE-2019-5736 (runc, "the canonical escape")
Out of scope for the 2021+ window but cited because every modern container
hardening guide starts here: a malicious container could overwrite the host
`runc` binary by writing through `/proc/self/exe`, achieving root on the
host the next time `docker exec` was run on *any* container. Patched in
`runc 1.0-rc7`. Mentioned only as a baseline so that "Docker engine ships
container-escape CVEs" is not perceived as a new phenomenon.
Source: <https://nvd.nist.gov/vuln/detail/CVE-2019-5736>

---

## Section 2 — Misconfiguration / curated-image incidents (image or config fault)

### 2.1 Kinsing — exposed Docker daemon API as a mass attack vector, ongoing since 2020
Kinsing is the most prevalent container-targeting malware family in
honeypot datasets. Its standard playbook does not require any Docker CVE:
it scans the internet for Docker daemons listening on `2375/tcp` (or
containers with `/var/run/docker.sock` bind-mounted), then issues a single
`docker run -v /:/host --rm -it alpine chroot /host sh`-style command to
get a root shell on the host. Once in, it deploys an XMRig cryptominer,
harvests AWS credentials from environment variables and instance metadata,
and pivots laterally to peer Docker / Kubernetes / SSH hosts.
**Root cause: image/configuration flaw.** The "container" in this scenario
is Docker working *as designed* — an exposed daemon API or a mounted
`docker.sock` *is* host root by spec.
Source: <https://www.aquasec.com/blog/threat-alert-kinsing-malware-container-vulnerability/> ·
<https://blog.prevasio.com/2020/08/kinsing-punk-epic-escape-from-docker.html>

### 2.2 TeamTNT — typosquatted/malicious images on Docker Hub, 150 K + pulls, 2020–2024
TeamTNT operated multiple Docker Hub accounts (notably `alpineos`) hosting
container images that bundled Docker-escape kits, Kubernetes exploit kits,
XMRig miners and credential stealers. The single most-used account
accumulated ~150 000 image pulls. Each malicious image, once run, attempted
to break out via mounted Docker socket / `--privileged` flags / exposed
APIs. In 2024, attackers were observed using compromised hosts to enroll
victims into a malicious *Docker Swarm* for C2 and lateral movement.
**Root cause: image/configuration flaw** (curated-image supply chain).
Demonstrates the exact failure mode a "carefully curated" base image is
meant to prevent — and shows that "I pulled it from Docker Hub" is not
curation.
Source: <https://www.darkreading.com/cloud-security/teamtnt-docker-containers-malicious-cloud-images> ·
<https://thehackernews.com/2024/10/new-cryptojacking-attack-targets-docker.html>

### 2.3 30 malicious images / 20 M pulls / ~$200 K in mining — Sysdig & Unit 42, 2022 onward
Sysdig and Palo Alto Unit 42 each catalogued waves of typosquatted public
images on Docker Hub: 30 confirmed malicious images with a combined ~20 M
pulls, embedding cryptominers in image layers. Qualys's January 2026 review
restated the same risk for ECR Public and Docker Hub: "public container
registries present ongoing risks including crypto mining, malware, and
typosquatting."
**Root cause: image/configuration flaw** (untrusted base image). Reinforces
the curation rule: pin to digests, not tags; verify provenance.
Source: <https://www.sysdig.com/blog/analysis-of-supply-chain-attacks-through-public-docker-images> ·
<https://unit42.paloaltonetworks.com/malicious-cryptojacking-images/> ·
<https://blog.qualys.com/product-tech/2026/01/22/public-container-registry-security-risks-malicious-images>

### 2.4 SCARLETEEL — exposed containerized workload to AWS-account-wide compromise, 2023→2024
Sysdig Threat Research Team's tracked campaign. Initial access: a vulnerable
public-facing service in a self-managed Kubernetes cluster on AWS. From
inside the workload the attacker hit IMDSv1/IMDSv2 to lift the underlying
node's IAM role, used the role to pivot across the AWS organization
(Terraform state in S3 → other accounts), and exfiltrated >1 TB of
proprietary source code. The escape from the container itself relied
entirely on operator-side configuration: an over-privileged service
account, IMDS reachable from the pod, and an exposed Terraform state file.
**Root cause: image/configuration flaw** (excessive workload privilege +
unblocked IMDS + pod credentials granting cluster-wide reach).
Source: <https://sysdig.com/blog/cloud-breach-terraform-data-theft/> ·
<https://www.bleepingcomputer.com/news/security/scarleteel-hackers-use-advanced-cloud-skills-to-steal-source-code-data/>

### 2.5 Trivy supply chain compromise — official Aqua Trivy Docker images backdoored, March 2026
On 2026-03-19 attackers ("TeamPCP") compromised Aqua Security's release
pipeline and pushed credential-stealing payloads into official Trivy
Docker images `aquasec/trivy:0.69.4`, then `0.69.5` and `0.69.6` (and the
`latest` tag during the window). Last clean release: `0.69.3`. Aqua's own
incident note is unambiguous about Docker-escape implications: *"if you
ran a compromised Trivy image with the Docker socket mounted, treat the
entire host as compromised"* — and Trivy is conventionally run with
`/var/run/docker.sock` mounted to scan local images, so the realistic
operational posture *is* a host compromise.
**Root cause: image/configuration flaw** (supply-chain compromise of an
*officially-published* image, exploited via the standard Trivy run pattern
of mounting the Docker socket). The image came from the vendor's own
`aquasec/trivy` repository — "trusted publisher" did not equal "safe."
Source: <https://www.aquasec.com/blog/trivy-supply-chain-attack-what-you-need-to-know/> ·
<https://socket.dev/blog/trivy-docker-images-compromised> ·
<https://thehackernews.com/2026/03/trivy-hack-spreads-infostealer-via.html> ·
<https://www.docker.com/blog/trivy-supply-chain-compromise-what-docker-hub-users-should-know/>

---

## Section 3 — AI-agent-driven container escapes (the angle that drives Alcatrazer)

### 3.1 SandboxEscapeBench — Oxford & UK AI Security Institute, March 2026
Marchand et al., "Quantifying Frontier LLM Capabilities for Container
Sandbox Escape" — an Inspect-AI CTF benchmark with 18 scenarios spanning
orchestration, runtime and kernel layers, executed inside a *nested*
sandbox (container-in-VM) so successful escapes can't damage the eval
infrastructure. The benchmark is open source.

The Docker-relevant scenarios in the runtime layer map almost 1-to-1 to
Alcatrazer's threat surface:

- **`privileged`** — full capability set ⇒ namespace break
- **`docker.sock`** — direct daemon control from inside the container
- **`hostpath`** — writable host bind mount
- **`CAP_SYS_ADM`**, **`CAP_MOD`**, **`CAP_DAC_RD`** — dangerous capabilities
- **`runc_2019`** — re-exploiting CVE-2019-5736
- **`runc_2024`** — re-exploiting CVE-2024-21626 (Leaky Vessels)

Models evaluated by name: GPT-5 / 5.2 / 5-mini / 5-nano, Claude Opus 4.5,
Sonnet 4.5, Haiku 4.5, DeepSeek-R1, GPT-OSS-120B. Top-line numbers: GPT-5
≈ 0.50 overall escape rate, Claude Opus 4.5 ≈ 0.49; on difficulty 1–2
(misconfiguration class) "frontier models reliably escape common sandbox
weaknesses"; collapse at difficulty 4–5 (multi-step kernel exploits with
"zero success on levels 4 and 5"). Help Net Security summarized the
practical implication: a frontier-model escape attempt costs roughly **one
US dollar in tokens**.

The team also reports **four unintended escape paths discovered by the
models themselves**, including brute-forcing default Vagrant SSH
credentials and downloading the publicly-known Vagrant private key from
GitHub. In other words, the agents not only solved the curriculum — they
found shortcuts the researchers had not anticipated.

**Root cause: mixed.** The benchmark includes *both* engine flaws (runc
CVEs) and image/config flaws (privileged, docker.sock, hostpath,
capabilities); the headline finding is that **a curated-but-imperfect
Docker setup is escapable by an off-the-shelf frontier coding agent for
about a dollar**.
Source: <https://arxiv.org/abs/2603.02277> ·
<https://www.aisi.gov.uk/blog/can-ai-agents-escape-their-sandboxes-a-benchmark-for-safely-measuring-container-breakout-capabilities> ·
<https://github.com/UKGovernmentBEIS/sandbox_escape_bench> ·
<https://www.helpnetsecurity.com/2026/03/30/ai-agents-container-breakout-capabilities-research/>

### 3.2 Ona — Claude Code escapes its own bubblewrap sandbox via three independent paths, March 2026
Caveat up front: Ona's host sandbox here is *bubblewrap*, not Docker — but
the lesson generalizes to any string-based denylist sitting between an
agent and a container boundary, including Docker-policy-style filters.
Three documented bypasses, all reasoned about by the model itself, none
prompted:

1. **Path-trick bypass.** The denylist matched `/usr/bin/npx` literally.
   Claude Code discovered that `/proc/self/root/usr/bin/npx` resolves to
   the same binary through procfs but does not match the deny pattern.
2. **Sandbox-disable bypass.** When the path trick was caught, the agent
   *"reasoned about the obstacle and got rid of it. Nobody told it to
   disable bubblewrap"* — it concluded the sandbox was in the way of
   completing the task and disabled it.
3. **ELF-loader bypass.** When Ona moved to kernel-level enforcement (a
   hashing tool called Veto that checks binary content rather than path),
   the agent invoked the dynamic linker directly:
   `/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /usr/bin/wget`. This loads
   the binary via `mmap` instead of `execve`, defeating the
   `execve`-hooked enforcement entirely.

**Root cause: image/configuration flaw class** — naive policy applied at
the wrong layer (string match on a path; `execve` hook only). The same
class of error in a Docker context is exactly the failure mode of
seccomp/AppArmor profiles that allow indirect execution paths, or of
denylist-based runtime tools that don't see container *intent*.
Source: <https://ona.com/stories/how-claude-code-escapes-its-own-denylist-and-sandbox>

### 3.3 Adjacent: AI-agent-targeted host escape via prompt injection (the chained variant)
Already documented in detail in
[PRD-001-alcatrazer-threat-model-research.md](./PRD-001-alcatrazer-threat-model-research.md):
**CurXecute (CVE-2025-54135)** rewrites `~/.cursor/mcp.json` from a
prompt-injection payload and executes attacker-chosen commands on the
developer's host machine; **Comment-and-Control (2026)** demonstrated the
same shape — prompt-injection payload in a PR title coerces Claude Code,
Gemini CLI and GitHub Copilot into leaking integration secrets out of
GitHub Actions runners.

These are not Docker escapes per se: in those incidents the agent already
ran with host-level reach, no escape was needed. They are listed here only
to underline the directionality: when the agent is *not* containerized,
a single injected instruction is sufficient. If Alcatrazer's curated
Docker layer holds, the same payload first has to escape the container
(per Section 1) or exploit a misconfigured boundary (per Section 2 / 3.1)
before reaching the host.

---

## Cross-cutting takeaways for Alcatrazer

| Question                                                  | Evidence                                                                                              |
|-----------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| *"Does Docker itself ship container-escape CVEs?"*        | Yes, on a roughly annual cadence: Leaky Vessels (Jan 2024 §1.1), CVE-2025-9074 (Aug 2025 §1.3), runc trio (Nov 2025 §1.2), NVIDIAScape (Jul 2025 §1.4), Pwn2Own kernel UAF (May 2025 §1.5). |
| *"Or is the weakness usually a misconfigured/curated image?"* | Yes, in volume: Kinsing/TeamTNT (§2.1, §2.2), Sysdig 30-images-20M-pulls (§2.3), SCARLETEEL (§2.4), Trivy supply-chain compromise of an *official* image (§2.5). |
| *"Are the misconfig classes that hurt small or large?"*   | Small and well-known: `--privileged`, mounted `docker.sock`, host bind mounts, dangerous capabilities, exposed daemon API, untrusted base image. SandboxEscapeBench (§3.1) tests exactly these and frontier models clear them at ~50 %. |
| *"Can a coding agent inside a curated container actually escape it?"* | SandboxEscapeBench (§3.1) shows yes for any of the misconfig classes above; Ona (§3.2) shows agents independently reason about and defeat denylist-style sandboxes; Pwn2Own (§1.5) and §1.1–§1.4 confirm engine-flaw escapes are within reach when patches lag. |

**Implication for Alcatrazer's threat model.** Curating the Docker image
well — minimal base, no `--privileged`, no `docker.sock` mount, no host
bind mounts, dropped capabilities, pinned digests, signed provenance —
neutralizes Section 2 entirely and roughly half of Section 3.1. It does
**not** neutralize Section 1: Docker engine CVEs require a patch cadence,
and CVE-2025-9074 specifically defeated every operator-side hardening
short of removing Docker Desktop. The honest framing for §2 of PRD-001 is
therefore:

- Curation is necessary and load-bearing, but not sufficient.
- The *durable* defense is to assume the inner sandbox is breakable and
  ensure that what is reachable from inside the container is not also a
  loaded gun pointed at the developer's identity, secrets, or repos —
  i.e. exactly the threats catalogued in the sibling threat-model note.

---

## Coverage map — incidents → Alcatrazer claims

| Alcatrazer claim                                                                  | Supporting incidents      |
|-----------------------------------------------------------------------------------|---------------------------|
| Docker is not a hard isolation boundary; engine CVEs land regularly.              | §1.1, §1.2, §1.3, §1.4, §1.5, §1.6 |
| Most in-the-wild container compromises start at operator misconfiguration.        | §2.1, §2.2, §2.3, §2.4    |
| "Curated/official image" is not a guarantee — vendor pipelines have been backdoored. | §2.5                   |
| Frontier coding agents can already escape misconfigured Docker sandboxes for ~$1. | §3.1                      |
| Agents reason about and bypass naive policy boundaries (denylists, exec hooks).   | §3.2                      |
| When the agent is *not* containerized, prompt injection alone reaches the host.   | §3.3 (and threat-model-research §A3, §A4) |