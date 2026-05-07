---
prd_id: PRD-001
title: "Alcatrazer"
version: 1
status: approved
author: "Grzegorz Latuszek"
overview_sections: ["1. Overview"]
---

# PRD-001: Alcatrazer

## 1. Overview

Alcatrazer is a security tool that lets a developer delegate coding work to
AI agents on their personal machine — in any language, with any toolchain —
without exposing their secrets, their identity, or their public reputation.
At its core is a sealed, sandboxed environment in which agents code.
Anything they produce flows back to the developer's real repository under
the developer's name only after the developer has reviewed and chosen to
push it.

The vision is wide adoption across every coding style — from mainstream
stacks to niche languages — by keeping the security layer
language-agnostic and offering convenience layers above it for the
ecosystems most developers reach for first.

## 2. Problem Statement

AI coding agents now run as the developer who launched them. They inherit
every key, every credential, every dotfile, every browsing session — the
whole laptop. A single mistake (a hallucinated command, a prompt-injected
fetched page, a careless tool call) can leak those secrets, and worse,
push malicious code to public repositories under the developer's own name —
silently endangering everyone downstream.

Existing developer-environment tooling is engineered for *convenience* —
sharing credentials, forwarding agents, smoothing the trip from laptop to
container. That model is exactly wrong when the inhabitant is no longer a
trusted human. Developers who want AI productivity are left without a
purpose-built way to keep agents productive *and* contained.

## 3. Goals & Objectives

- **G1 — Protect the developer's machine.** Nothing on the host filesystem
  beyond the agent's assigned workspace is reachable from inside the
  agent's environment.
- **G2 — Protect the developer's reputation.** No code authored by an
  agent reaches a public repository under the developer's identity
  without the developer's explicit review.
- **G3 — Keep agents productive.** Agents retain everything they need
  to do real work: code, branch, merge, test, install packages, talk
  to LLMs, read the web.
- **G4 — Disappear into the workspace.** Agents inside the sandbox
  cannot tell that Alcatraz is the thing surrounding them.
- **G5 — Earn trust by proof, not assertion.** Security claims are
  verifiable by the developer on their own machine and against
  independent sources, not taken on faith.
- **G6 — Stay easy to adopt.** Adding Alcatrazer to an existing
  repository, and operating it day-to-day, requires only a small
  number of simple commands and almost no domain knowledge of the
  underlying isolation technology.
- **G7 — Reach any coding environment.** The security core is
  language-, toolchain-, and package-manager-agnostic: any environment
  a developer can install inside the sandbox is supported. Convenience
  layers exist on top for the most common ecosystems so those
  developers can be coding-ready in a few lines of declaration; rarer
  ecosystems (Haskell, Perl, PHP, Elixir, niche toolchains) remain
  reachable today through the security layer alone, and the roadmap
  is to make the convenience layer fully language-independent.

## 4. Target Users / Personas

| Persona | Description | Key Needs |
|---|---|---|
| **Solo developer using AI coding agents** | A programmer who runs AI coding agents (e.g. Claude Code or similar) on their personal laptop, against repositories they own. They have real credentials, real personal data, and a public identity tied to repositories that other people depend on. | Confidence that agents cannot reach laptop secrets; confidence that nothing reaches their public repositories without review; minimal day-to-day overhead. |

The product is deliberately scoped to the single-developer workflow.
Multi-developer, shared-workstation, and team-orchestration models are
out of scope.

## 5. User Stories / Use Cases

- **US-1** — As a developer, I can add the secure agent environment to
  any of my existing repositories with a small number of simple
  commands.
- **US-2** — As a developer, my agents start from the current state of
  my work — whatever branch I am on at the moment I bring the
  environment up — not from scratch and not from some other branch.
- **US-3** — As a developer, the work my agents produce arrives in my
  real repository on that same starting branch, under my name,
  automatically — so I can review it with the same tools I already
  use.
- **US-4** — As a developer, I can keep editing my real repository at
  the same time my agents are working. If I do something that would
  put my repository in conflict with their work, the system safely
  holds rather than overwriting either side.
- **US-5** — As a developer, when I switch to a different branch in
  my real repository, agent work transfer to my real repo pauses safely; 
  when I return to the starting branch, it picks up automatically 
  without me restarting anything.
- **US-6** — As a developer, if I ask to tear down the environment
  while agent work is held and not yet in my repository, the system
  tells me and lets me decide whether to drop that work or recover
  it.
- **US-7** — As a developer, I can step into the sandbox to observe
  and steer the agent without that giving the agent any way back to
  my host.
- **US-8** — As a developer, I can verify on my own machine that the
  security model actually holds, without having to trust the tool's
  marketing.
- **US-9** — As a developer working in any language or toolchain, I
  can use Alcatrazer's security layer; for the common ecosystems I
  can additionally rely on built-in convenience so my agents are
  ready to code immediately.
- **US-10** — As a developer who cares about supply-chain integrity,
  I can verify the source of the tool I am running against an
  independent channel.

## 6. Functional Requirements

> Requirements describe **what** the product does, not how. Today's
> implementation choices (the specific isolation technology, the exact
> command names, the on-disk layout) are deliberately not encoded
> here. The product is structured as a **security core** that any
> coding environment can sit on top of, and a **convenience layer**
> above it that makes the most common ecosystems frictionless. The
> security core is the product; the convenience layer is supporting
> infrastructure.

### Core: Sealed Coding Sandbox

| ID | Requirement | Priority | Acceptance Criteria |
|---|---|---|---|
| FR-1 | Agents operate inside a sealed sandbox with no path to the host filesystem outside their assigned workspace. | Must | No host home, no dotfiles, no developer credentials, no project paths beyond the workspace are reachable from inside. |
| FR-2 | LLM credentials are exposed to the sandbox only through a narrow, explicit, read-only channel — never by sharing the developer's broader configuration. | Must | The developer's wider tool/IDE configuration stays on the host. |
| FR-3 | Outbound network access is permitted (LLM APIs, package registries, documentation, web). Inbound access is not exposed. | Must | Agents can reach what they need to be productive; the sandbox is not a network-restricted environment. |
| FR-4 | Even on a sandbox escape, the agent process holds no host privileges and cannot write to host-owned files. | Must | The escape attack surface yields no write capability on host-owned paths. |
| FR-5 | The mechanism that defines the sandbox is generated by the tool from the user's environment declaration, not authored by the user. The security base is under the tool's control; user-provided content layers on top. | Must | The user does not need to learn or maintain the underlying isolation technology. |
| FR-6 | The isolation mechanism is treated as an internal contract, not part of the user-facing surface. The product is designed so that the underlying technology can evolve (e.g. from one container runtime to another, or to a different sandbox class entirely) without changing the user contract. | Must | Concept boundaries — sandbox lifecycle, workspace mount, identity, network, credentials — are defined independently of any single backing technology. |

### Core: Workspace & Identity Isolation

| ID | Requirement | Priority | Acceptance Criteria |
|---|---|---|---|
| FR-7 | The agent's workspace is initialized from the current state of the developer's branch at bring-up — content only, with no inherited history of who worked on what before. | Must | Agents see today's code; they do not see prior commit history, prior author identities, or internal references. |
| FR-8 | The workspace is sealed: no remote pointing back to any real repository, no shared state with the host's version control. | Must | The agent cannot push, fetch, or otherwise communicate with the developer's real remotes. |
| FR-9 | The workspace is located, named, and labeled in a way that does not reveal the existence of Alcatrazer. | Must | An agent inspecting its surroundings sees a generic working environment. |
| FR-10 | Agent commits are authored under a randomly generated, plausible human identity that does not match the real developer. | Must | The agent's git history shows a believable but fictitious author. |

### Core: Stealth (Principle 2)

| ID | Requirement | Priority | Acceptance Criteria |
|---|---|---|---|
| FR-11 | Nothing inside the agent's environment — files, configuration, environment variables, mounts, process names, commit metadata — identifies Alcatrazer as the surrounding tool. | Must | An automated check from inside the sandbox finds zero footprint of the tool. |
| FR-12 | Operational machinery (such as the promotion mechanism) runs entirely outside the sandbox; the inside of the workspace is observed, never written into, by the host-side controller. | Must | The sandbox shows no trace of the controller. |

### Core: Promotion of Agent Work

Promotion is a term we use to describe agent work transfer to real repo on host machine.
```
    Host repo                    Alcatraz repo
    git HEAD   <--- promote ---  git main/
```

| ID | Requirement | Priority | Acceptance Criteria |
|---|---|---|---|
| FR-13 | Promotion is bound to a single **starting branch** — the branch the developer was on when the secure environment was brought up. Agent work is taken from that starting point and returns to that same branch in the developer's repository. There is no cross-branch promotion. | Must | The starting branch is recorded once at bring-up; agents start from it; promoted commits append to it. |
| FR-14 | Promoted commits arrive in the developer's repository as new commits on top of its current tip; the existing history is never rewritten and the working tree remains consistent with the latest promoted state. | Must | After any promotion event, prior commits remain ancestors of the new tip and the repository is in a sane, recoverable state. |
| FR-15 | Every promoted commit is rewritten so that authorship belongs to the developer, not the agent's fictitious identity. | Must | The outer repository shows the developer as author and committer; the fictitious identity does not appear there. |
| FR-16 | Promotion is unidirectional (agent → developer), incremental (only new work is moved), idempotent (running it when nothing is new is a no-op), and runs automatically in near-real-time. | Must | Repeated promotion never duplicates, reorders, or corrupts commits; latency in steady state is small and bounded. |
| FR-17 | When the developer's repository drifts into a state where promotion cannot safely proceed — the developer has switched away from the starting branch, or has changes on the starting branch that would conflict with agent work — promotion is **held** rather than overwriting anything. The developer's repository is never modified while in a held state. | Must | The held state is observable; the starting branch is not modified while held; no agent work is silently lost. |
| FR-18 | Promotion auto-resumes the moment the safe condition is restored (e.g. the developer returns to the starting branch, or resolves the conflicting state). No manual restart of the tool is required. | Must | Returning to the starting branch flushes pending agent work without further user intervention. |
| FR-19 | When the developer takes an action that requires a decision — e.g. asking to tear down the environment while agent work is still held and not yet in the repository — the system surfaces the situation explicitly and offers the developer a clear choice: drop the pending work, or recover it. | Must | A teardown attempt with held work prompts the developer; non-interactive teardown requires an explicit drop instruction. |
| FR-20 | The developer can inspect Alcatrazer at any moment. That includes its static/configured part as well as the live state of the promotion machinery (active, held, or otherwise blocked) and the count of work pending promotion. | Must | A status surface reports this without requiring log inspection. |

### Onboarding & Lifecycle

| ID | Requirement | Priority | Acceptance Criteria |
|---|---|---|---|
| FR-21 | A small set of simple commands covers the full lifecycle: set up the environment in a repository, bring it up, step inside, suspend, reset, verify, and inspect status. | Must | A new user can complete the full loop without learning the tool's internals. |
| FR-22 | Setup is interactive by default, with a non-interactive mode for automation. The user is asked only what the tool cannot infer. | Must | Re-running setup against an already-configured repository offers to reuse prior choices. |
| FR-23 | Bring-up is self-correcting: the tool detects when it must rebuild the sandbox image, restart the runtime, or skip work, and acts accordingly. The user does not manage these states by hand. | Must | A configuration change between runs is picked up automatically. Rebuild loops or stale-image surprises do not occur. |
| FR-24 | Suspend and reset are idempotent. Any agent commits eligible for promotion are flushed before the runtime is torn down (FR-19 covers the held-and-pending case). | Must | A graceful suspend/reset followed by bring-up shows zero lost commits in the un-held case. |

### Trust, Verification, and Auditability

| ID | Requirement | Priority | Acceptance Criteria |
|---|---|---|---|
| FR-25 | A bundled verification suite, runnable by the developer on their own machine, validates the security model end-to-end (sandbox isolation, credential boundary, identity rewriting, stealth, no host-side privilege leakage). | Must | A single command runs it; failures are actionable. |
| FR-26 | The installed source is shipped readable, never obfuscated. The developer can audit what they are running. | Must | All installed source is human-readable plain code. |
| FR-27 | Every release publishes integrity-verifiable checksums via a channel independent of the package distribution channel, so that the developer can verify the tool against an external trust anchor. | Must | Standard system tooling is sufficient to perform the check; no Alcatrazer-supplied script is required. |
| FR-28 | The product's core carries no third-party runtime dependencies, keeping the auditable surface small. | Must | The trust boundary is the language standard library plus the project's own source. |

### Convenience Layer: Coding-Environment Declaration

> The security core above is the product. The capabilities below are
> a convenience layer that lets agents start coding immediately on
> bring-up rather than waiting for the developer to install
> dependencies inside the sandbox by hand. **A developer working in
> a language or toolchain not covered by this layer can still use
> Alcatrazer**: the sandbox is general-purpose; the security layer
> applies regardless.

| ID | Requirement | Priority | Acceptance Criteria |
|---|---|---|---|
| FR-29 | The product is language-, toolchain-, and package-manager-agnostic at its core. The security layer functions for any coding environment the developer can install inside the sandbox. | Must | A developer working in a language not on the convenience list (Haskell, Perl, PHP, Elixir, niche toolchains, …) can still use Alcatrazer by installing what they need inside the sandbox. |
| FR-30 | The agent's coding environment (languages, OS packages, startup commands) can optionally be described in a single, unbranded, version-controllable file at the root of the developer's repository. The convenience layer reads this declaration and pre-prepares the sandbox so agents are ready to code on bring-up. | Must | The file is natural to keep in version control; it does not mark the repository as Alcatrazer-managed. Absence of this declaration does not block the security core. |
| FR-31 | The format of that declaration is versioned. The tool refuses, with an actionable message, to operate on a declaration it does not understand, rather than guessing. | Must | Forward and backward incompatibility surface as clean errors, never silent misbehavior. |
| FR-32 | Built-in convenience templates exist for the major mainstream language ecosystems, so that a developer in those ecosystems can be coding-ready in a few lines of declaration. The supported set is data-driven and extensible without product redesign. | Must | New ecosystems can be added by extending the data set, not the control flow. |
| FR-33 | A roadmap exists toward a fully language-independent declarative format, so that any coding environment — including those not covered by built-in templates — can be expressed in the same declaration without requiring per-language tool support. | Should | Rare ecosystems may currently require more user effort; the design admits a path to first-class declarative support. |
| FR-34 | Some languages may carry implicit OS-level dependencies. These are merged into the user's declared OS packages without overriding the user's order or intent. | Must | The merged set is deterministic and predictable. |

## 7. Non-Functional Requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | **Footprint on the developer's repository.** Adding Alcatrazer to a repository introduces at most a single, unbranded environment-declaration file plus a credential-template file into version control. | Two committed artifacts; nothing else of Alcatrazer's enters the version-controlled tree. |
| NFR-2 | **Footprint on the developer's machine.** No system-wide installation; no host privileges required at any point in normal operation. | Per-repository install model; runs end-to-end as an unprivileged user. |
| NFR-3 | **Coexistence.** Multiple Alcatrazer-managed repositories on the same machine operate independently, without name collisions or shared state. | Two simultaneous instances on one laptop work without manual disambiguation. |
| NFR-4 | **Promotion latency.** Agent commits appear in the developer's real repository in near-real-time during normal use. | Delay bounded by a small, configurable polling cadence. |
| NFR-5 | **Stealth.** Zero leakage of Alcatrazer's identity into the agent's workspace (files, environment, processes, mounts, commit metadata). | Verified by an automated check that fails on any leaked identifier. |
| NFR-6 | **Auditable surface.** No third-party runtime dependencies in the core; installed source is plain readable code. | The sum of source the user must trust is the language standard library plus the project's own code. |
| NFR-7 | **Backend-agnosticism.** The architecture cleanly separates the sandboxing mechanism from the rest of the system, so the sandbox can be replaced by a different technology in the future without breaking the user contract. | Sandbox boundary is expressed as an internal contract, not as direct calls into a specific runtime. |
| NFR-8 | **Coding-environment universality.** The security core does not assume any particular language, package manager, or toolchain. | A developer in any environment can use Alcatrazer; the convenience layer is additive, not gating. |
| NFR-9 | **Platform reach.** Linux is the primary target; macOS is fully supported. | Both platforms run the full test suite. |

## 8. Scope

### In Scope

- A secure, isolated coding environment for AI agents working on a
  single repository, on a single developer's machine.
- A security core that is language-, toolchain-, and package-manager-
  agnostic.
- A convenience layer above the core, with built-in templates for the
  major mainstream language ecosystems (currently Python, Node.js,
  Rust, Go, .NET, Java).
- Automatic, near-real-time promotion of agent work back into the
  developer's real repository, on the same starting branch from
  which agents began.
- Concurrent human + agent work on the same repository, with safe
  hold-and-resume on conflict and explicit decisions when the
  developer's actions require one.
- A bundled, user-runnable verification suite that proves the
  security claims on the developer's own machine.

### Out of Scope

- **Multi-developer or team / shared-workstation deployments.** The
  product is designed around the single-developer workflow.
- **Multi-repository orchestration.** One repository at a time.
- **Network restriction.** Agents need outbound internet access for
  LLMs, registries, and documentation.
- **Build / deploy pipelines.** Alcatrazer addresses the *coding*
  loop only; building, packaging, and running the developer's own
  product remain the developer's concern.
- **A "developer-convenience" sandbox.** The product is a security
  tool, not a way to share preconfigured dev environments.
- **Pre-1.0 backwards-compatible migrations.** While the product is
  pre-release, breaking changes refuse old workspaces with an
  actionable upgrade message rather than carrying migration code.
- **Cross-branch promotion or branch-namespace remapping.** Agent
  work begins from, and returns to, a single starting branch.

## 9. Success Metrics / KPIs

> Outcome metrics chosen to survive implementation changes.

| Metric | Target | Measurement Method |
|---|---|---|
| Verification suite pass rate on supported platforms | 100 % | Bundled test suite |
| Reported credential-leak or identity-leak incidents attributable to Alcatrazer | 0 | User-facing issue tracker |
| Time from "I have a repository" to "an agent has produced its first promoted commit" | Minutes, not hours | Manual onboarding walk-through on a clean machine |
| Promotion latency in steady-state operation | Near-real-time | Promotion log timestamps |
| Detected leaks of the tool's identity into the workspace | 0 | Automated stealth check |
| Continuous unattended operation of the promotion machinery | Days, without manual intervention | Long-running session observation |
| Coverage of the convenience layer | Mainstream ecosystems supported with a handful of declaration lines; long-tail ecosystems usable through the security core alone | Onboarding walk-throughs per ecosystem |

## 10. Timeline / Milestones

> Capability stages, expressed independently of release numbers.

| Milestone | Status | Description |
|---|---|---|
| **M1 — Secure agent coding loop** | Shipped | A developer can add Alcatrazer to a repository, agents work in a sealed sandbox, and their commits flow back under the developer's identity. Bundled verification proves the security claims on the developer's machine. |
| **M2 — Convenience for mainstream ecosystems** | Shipped | Out-of-the-box convenience-layer templates for the major mainstream language ecosystems, so developers in those stacks are coding-ready in a few lines of declaration. |
| **M3 — Reliable promotion under concurrent work** | In flight | Promotion bound to a starting branch; preserves the developer's existing history; safely holds rather than overwrites when the developer's repository diverges; resumes automatically once the safe condition is restored; surfaces explicit decisions when the developer's actions require one. |
| **M4 — Universal coding-environment declaration** | Planned — **gates 1.0** | Language-independent declarative format for the convenience layer, so any coding environment — not just those with built-in templates — can be expressed cleanly. The first official release must open the door to every language environment, not only the mainstream ones; M4 is therefore a hard precondition for 1.0. |
| **M5 — Backend abstraction** | Planned — post-1.0 | The sandboxing mechanism is hidden behind a stable internal contract, so the underlying isolation technology can be swapped or extended without affecting the user contract. Not a gate for 1.0. |
| **M6 — 1.0** | Planned | Feature stability and a backwards-compatibility commitment for the user-facing contract. Preconditions: M3 (reliable promotion) and M4 (universal coding-environment declaration). M5 is desirable but not required. |

---

## 11. Dependencies

- A general-purpose host-side sandboxing technology available to the
  developer (today fulfilled by a widely-available containerization
  runtime; the product is designed not to be tied to that choice).
- An LLM provider's authentication artifact accessible on the host.
- A package distribution channel and an independent integrity channel
  for verification.

## 12. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The promotion mechanism damages the developer's repository under concurrent edits. | High | Realized once during pre-release testing. | Addressed by milestone M3: hold-rather-than-overwrite, history preservation, explicit decisions when needed. |
| The developer attaches an IDE that itself relays host credentials into the sandbox, defeating isolation. | High | Medium. | Documented attach paths; the security model identifies which attach surfaces are safe and which are not. |
| An adversarial agent identifies the surrounding tool and targets known weaknesses. | Medium | Continuous. | Stealth is a hard requirement (G4); enforced by automated checks. |
| The convenience layer leaks into being perceived as the product, biasing users away from non-built-in languages. | Medium | Continuous. | Documentation, marketing, and the PRD itself frame the security core as the product and the convenience layer as additive. |
| A required capability tempts the project into adding a third-party runtime dependency, growing the audit surface. | Low | Continuous. | Dependency-free posture is an explicit, revisitable architectural decision rather than a quiet drift. |
| The developer pushes promoted commits without reviewing them. | Medium | Medium. | Push happens only from the developer's repository; the workflow is designed to invite review before push. |

## 13. Technical Considerations

- **Security core, convenience above.** The architecture separates
  the security boundary (sandbox, identity, stealth, promotion,
  verification) from the convenience layer (coding-environment
  declaration). A failure or absence of the convenience layer must
  not weaken the core.
- **Backend-agnostic core.** The sandbox boundary is an internal
  abstraction; the rest of the system does not assume any specific
  isolation technology.
- **Snapshot, not clone.** Agents start from the current content of
  the developer's branch at bring-up, not from inherited history.
- **Starting-branch contract.** The branch the developer is on at
  bring-up defines both the workspace's starting point and the
  destination of promoted work. Cross-branch behavior is not part of
  the model.
- **Hold-rather-than-overwrite.** When the developer's repository
  cannot safely receive promoted work, the system holds (but agents can still code);
  it never silently modifies the developer's branch.
- **Host-side controller.** Operational machinery lives outside the
  sandbox, observing rather than inhabiting the workspace.
- **No third-party runtime dependencies in the core.** The trust
  surface is intentionally small.

## 14. Open Questions

- [ ] Windows: aspirational support, or formally out of scope?
- [ ] Should adoption metrics (e.g. download counts, public stars) be
  tracked, or is the project deliberately not optimizing for them?