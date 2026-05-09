# Licence Change: MIT → Apache-2.0

## What's in this document

This document explains a single change: Alcatrazer's open-source licence is moving from **MIT** to **Apache-2.0**. The release that ships this change contains no other code changes — only the `LICENSE` file is updated.

We're shipping the licence change as a dedicated release, with this document alongside it, because Alcatrazer is built on **verifiable trust**: you should understand what you're depending on, not take "this is fine" on faith. That posture applies to the licence too.

If you're not a lawyer — neither are we. This document explains what the change means in plain language, with concrete scenarios, so you can decide whether you're comfortable with it.

## TL;DR

- Both licences are **permissive open-source**. You can still: use, modify, fork, distribute, and commercialise Alcatrazer under both.
- Apache-2.0 adds **explicit guarantees** — about patents, about trademarks, about who's protected from whom — that MIT leaves unwritten.
- The change is **a paperwork update**, not a strategy shift. Alcatrazer's source, behaviour, and openness do not change.
- **You almost certainly don't need to do anything.** If you fork or redistribute Alcatrazer, read the "Migration" section near the bottom.

## Why we're explaining this

Alcatrazer's whole point is *verifiable trust*: we want you to be able to prove the security claims, read the source, audit the dependencies, and understand the trade-offs. We don't want users who trust the tool because we said so — we want users who trust the tool because they checked.

That posture applies to the licence too. A licence change can look bureaucratic. It is not. A licence is the contract between the project and you, the developer using or depending on it. Quietly swapping it without explanation would be the opposite of how this project tries to operate. So we're being explicit: here is exactly what changes, with concrete scenarios for the parts that aren't obvious.

The same transparency applies to the dependency graph behind the licence. If you want to verify that *Alcatrazer's actual dependencies are compatible with Apache-2.0*, rather than take our word for it, the companion document [`license_dependencies_and_usage.md`](./license_dependencies_and_usage.md) walks through every component Alcatrazer depends on at build time, distribution time, and runtime — with the verification commands you can run to reproduce the analysis on your own checkout.

If anything below is unclear, please open an issue — clarifying it for one person almost always means clarifying it for many.

## What's the same under both licences

Before the differences, the things that don't change:

- **You can use Alcatrazer for anything.** Personal, commercial, in a startup, in a Fortune 500, in a research project. No royalties, no fees, no field-of-use restrictions.
- **You can modify the source.** Fork it, patch it, integrate it into your own tools.
- **You can redistribute.** Ship Alcatrazer (or a modified version) as part of your own product, library, or service.
- **You must preserve the copyright notice and the licence text** in distributions. Both licences require this; the mechanism is slightly different (more on NOTICE below) but the spirit is identical.
- **You get the software "as is."** Both licences disclaim warranties.

If you were happy with MIT, the things you cared about under MIT are also true under Apache-2.0.

## What's different, in plain English

The key differences live in four places: **patents**, **trademarks**, an **attribution mechanism (NOTICE)**, and **compatibility with the GPL**. Here is the short comparison, then plain-English explanations with scenarios.

| Dimension | MIT | Apache-2.0 |
|---|---|---|
| Patent grant | Implicit only (not written down) | **Explicit grant** + termination if you sue over the project's patents |
| Trademark grant | Silent | **Explicitly disclaimed** — the licence does not grant rights to the project name or logo |
| Attribution mechanism | Preserve copyright + licence text | Preserve copyright + licence text + propagate `NOTICE` file if one exists |
| Modification disclosure | Not required | Modified files must carry a "modified" notice |
| GPL compatibility | Compatible with GPLv2 and GPLv3 | Compatible with GPLv3 only (not GPLv2) |
| Document length | ~170 words | ~1,700 words |

The first two — patents and trademarks — are the load-bearing changes for you, the user. The rest are mostly administrative.

### Patent Grant — explicit protection from rug-pulls and contributor-weaponised lawsuits

#### What "software patents" are, briefly

In the US (and a few other places), you can get a government-issued patent on a *technique* — "a method for doing X." For example, someone could hold a patent on "a method for routing commits between an isolated execution environment and a developer's repository under a rewritten author identity." If you use that technique without permission, the patent holder can sue you for damages, even if you wrote your own code from scratch.

Software patents are widely regarded as a mess (vague, broad, easy-to-grant), but they exist and they get asserted.

#### The MIT-vs-Apache-2.0 difference

When the Alcatrazer project hands you the code under a licence, it is giving you permission to use it. The question is: **does that permission also cover any patents the project's contributors hold over the techniques in the code?**

- **MIT:** silent. Most lawyers will tell you there is an "implicit" patent licence — meaning if a contributor publishes code and tells you "use it," they probably can't turn around and sue you over patents on the same code. But it is not written down. Implicit isn't bulletproof.
- **Apache-2.0:** says it explicitly. *"We grant you a patent licence to whatever patents we hold that this code uses, royalty-free, forever."* Plus a catch: **if you sue anyone over a patent claim about this code, your patent licence terminates immediately.** That's the "patent termination" clause.

#### Three scenarios where this could hurt a developer using Alcatrazer

##### Scenario 1 — A future maintainer turns hostile

Imagine a future where Alcatrazer's project is controlled by someone with different incentives — perhaps the original maintainer sells the project, or a successor takes over and chooses a commercial path. They file a patent on Alcatrazer's promotion machinery. Then they send legal letters to companies still using the open-source Alcatrazer, demanding licensing fees.

- **Under MIT:** your company's lawyers are now arguing implicit-licence theory in court. They might win. But they are spending real money to defend something the licence never spelled out.
- **Under Apache-2.0:** the licence explicitly says you were granted a perpetual, royalty-free patent licence over Alcatrazer's techniques. The legal letter goes in the trash.

This is the "rug pull" scenario. Apache-2.0 prevents it; MIT leaves it ambiguous.

##### Scenario 2 — A contributor turns hostile

Suppose someone contributes a clever optimisation to Alcatrazer. Later they go work at MegaCorp, where MegaCorp owns a patent that arguably covers their contribution. MegaCorp decides to assert that patent against your startup, which uses Alcatrazer.

- **Under MIT:** MegaCorp can sue you and keep using Alcatrazer themselves with no consequence.
- **Under Apache-2.0:** the moment MegaCorp files the lawsuit, MegaCorp's right to use Alcatrazer (granted via their employee's contribution) **terminates automatically**. So MegaCorp's lawyers, before authorising the lawsuit, have to ask: *"Do any of our employees or subsidiaries depend on Alcatrazer for anything?"* If yes, suing your startup means losing internal access to a tool MegaCorp uses elsewhere. That's a real cost to suing — and patent lawyers hate uncertainty about hidden costs. The lawsuit gets less likely.

This is the "mutual disarmament" effect. It is the part the Apache Foundation specifically designed in. MIT has no equivalent.

##### Scenario 3 — Your corporate legal team refuses adoption

Your team wants to use Alcatrazer at a Fortune-500 company. The submission goes to legal review.

- **Under MIT:** *"Patent grant is implicit, not explicit. We can't quantify the risk. Recommend declining."* Adoption blocked.
- **Under Apache-2.0:** *"Standard permissive licence with explicit patent grant. Approved."* Adoption proceeds.

This is not hypothetical — many enterprise legal teams have written internal policies treating MIT and Apache-2.0 differently for exactly this reason. If Alcatrazer is to be adoptable inside larger companies — and we want it to be — Apache-2.0 reduces friction.

#### When Apache-2.0 does NOT help with patents

If a **third-party patent troll** (no connection to the project) holds a patent and sues a developer using Alcatrazer, **neither licence helps**. Both MIT and Apache-2.0 only govern the relationship between the project's contributors and the users. A random external patent troll is outside that relationship. For that risk you need patent insurance, defensive patent pools (like LOT Network or Open Invention Network), or the Unified Patents kind of subscription. Neither licence is a magic shield against arbitrary patent trolling.

So Apache-2.0's protection is specifically: **the project can't turn against you, and contributors are deterred from weaponising their patents against the community**. That is narrower than "patent-proof," but it is a real, named protection MIT lacks.

#### One sentence

**MIT says "use the code."** **Apache-2.0 says "use the code, and we explicitly promise we won't sue you over patents we hold on it, and any of us who tries to sue someone over patents on this code automatically loses access to it themselves."**

For a security tool that other developers will rely on, the explicit guarantee is more valuable than the implicit one — and it costs nothing to provide.

### Trademark Grant — protecting the project name from impersonation

#### What a trademark is, briefly

Trademark law is *separate* from copyright. Copyright says "who can copy/modify the code." Trademark says "who can use the project's name and logo to identify a thing as 'this project.'" Even if a licence lets you copy the code, it does not automatically let you call your copy "Alcatrazer."

#### The MIT-vs-Apache-2.0 difference

- **MIT:** silent on trademarks. Most lawyers say there is no implicit licence either — but it is not written down. So if someone forks the project and uses the name confusingly, the only defence is general trademark law, which can require formal trademark registration in the relevant jurisdictions to enforce strongly.
- **Apache-2.0 §6** explicitly says: *"this licence does not grant you a licence to use the trademarks, trade names, service marks, or product names of the Licensor."* It forecloses any "but the licence let me use the name" defence from a bad-faith fork.

#### Concrete scenario — where this could hurt a developer using Alcatrazer

A bad actor takes Alcatrazer's source, modifies it to add a backdoor (or a credential-exfiltration step in the promotion machinery — exactly the kind of attack Alcatrazer is designed to prevent), and publishes a binary called *"Alcatrazer Pro — faster, more compatible."*

- A developer searches for "Alcatrazer download," finds "Alcatrazer Pro" near the top of results, installs it. Their credentials get harvested through the very tool they trusted to protect them.
- **Under MIT:** the project's cease-and-desist letter rests on general trademark law. The bad actor argues *"MIT permitted me to fork; the name was implicitly available."* Lawyers on both sides. Slow. Expensive.
- **Under Apache-2.0:** the cease-and-desist letter quotes §6 verbatim. The licence *itself* confirms the bad actor has no name licence. Faster takedown. The hosting provider, app store, etc. find Apache-2.0 §6 a much cleaner basis to honour the takedown request than a vague "trademark dispute."

For a **security tool** specifically, brand integrity has real security value: a user who downloads "Alcatrazer" should be able to trust it is the real project. Apache-2.0 doesn't *create* trademark protection (you still need to actually pursue any infringement; jurisdictional registration matters for the strongest protection). What it does is **explicitly foreclose the implicit-licence defence**, which is the single most common bad-faith argument.

This is a meaningful upgrade for a tool whose users are betting their credentials on its integrity.

### NOTICE File — explicit attribution mechanism for downstream redistributors

#### What it is

Apache-2.0 §4 introduces a specific mechanism: if the project ships a file called `NOTICE` in its repo, anyone who distributes derivatives must include a copy (or at least the relevant parts) in their distribution. The `NOTICE` typically contains: project name, copyright notices, attributions to third-party code used, optional legal disclaimers.

MIT requires preserving the copyright + licence text, but has no separate "NOTICE" mechanism — attribution gets done by shipping the LICENSE file with the copyright header.

#### Concrete scenario

Imagine a startup builds a developer-tools product that bundles Alcatrazer.

- **Under MIT:** they must include the LICENSE file (or at least the copyright + licence text) somewhere in their distribution. That is it.
- **Under Apache-2.0 with a NOTICE file:** they must include the NOTICE text too — typically in their About dialog, LICENSES folder, or app-bundle resources. So if Alcatrazer's NOTICE says *"Alcatrazer, Copyright © Alcatrazer contributors"*, that text propagates downstream.

#### What this means for you in practice

- If Alcatrazer doesn't ship a NOTICE file, this provision is dormant — your obligations as a redistributor are effectively the same as under MIT.
- If we later add upstream attributions (for example, if we adapt code from another project), the NOTICE mechanism is a clean, structured way to honour those attributions through downstream distributions.
- The administrative burden if a NOTICE exists is real but tiny: include the file, that's all.

If you are an end user (you run Alcatrazer, you don't redistribute it), this row of the comparison does not affect you at all.

### GPL Compatibility — when does this affect you?

#### What "licence compatibility" means

"Can I take code under licence A and combine it with code under licence B in the same product, without violating either licence?" The GNU GPL is "copyleft" — meaning if you distribute software containing GPL code, the whole distribution must be GPL too. Some licences are "GPL-compatible" (they can be combined with GPL code); some are not.

- **MIT** is compatible with both GPLv2 and GPLv3.
- **Apache-2.0** is compatible with **GPLv3 only**, not GPLv2. (The reason: Apache-2.0's patent termination clause counts as an "additional restriction" not present in GPLv2. GPLv3 was rewritten in 2007 specifically to accommodate it.)

#### When this could matter to you

You combine Alcatrazer's source with code that is licensed **GPLv2-only** (not GPLv2-or-later, not GPLv3). Concretely: the Linux kernel itself is GPLv2-only. So if you wanted to embed Alcatrazer's code in a kernel module, the licence compatibility would matter.

#### Why this is moot for almost everyone

- Alcatrazer has zero third-party runtime dependencies in the core (this is a deliberate design choice). So Alcatrazer is not going to *include* any GPL code — both licences cover Alcatrazer's own code only.
- Alcatrazer is a userspace developer tool, not a kernel module. Nobody is bundling it into the Linux kernel.
- Most GPL code in 2026 is GPLv3 or "GPLv2-or-later" — both are compatible with Apache-2.0.

A real-world scenario in which this affects an Alcatrazer adopter is hard to construct. We are flagging it for completeness; we don't expect it to matter for any current user.

## When Apache-2.0 still doesn't help

To stay honest, Apache-2.0 is not a magic shield. It does not protect you against:

- **Third-party patent trolls** — entities with no connection to the project asserting unrelated patents. (Discussed above under "When Apache-2.0 does NOT help with patents.")
- **Liability for how you use Alcatrazer.** Both licences disclaim warranties. If Alcatrazer fails to contain an agent and credentials leak, the licence will not be the basis for a claim against the project.
- **Data-protection / regulatory compliance.** That's your responsibility as the operator, not the project's responsibility as the supplier.
- **Trademark enforcement effort.** Apache-2.0 §6 makes the *legal basis* clearer; actually pursuing a brand-impersonating fork still requires the project (or its successor) to spend time and possibly money. The licence makes the case easier, not free.

We mention these to set expectations honestly. The licence change is a real upgrade in named protections, not a transformation into something it is not.

## Migration — what you need to do

For most users: **nothing.** If you currently use Alcatrazer, the licence change does not require any action on your part.

If you redistribute Alcatrazer (e.g. you ship it as part of your own tool, or you maintain a fork):

- **Update your bundled licence text** when you next pull Alcatrazer. The repo's `LICENSE` file is now Apache-2.0; copy the new file into your distribution as you normally would.
- **If a NOTICE file is added in the future**, include it in your distribution alongside the LICENSE.
- **If you mark modified files**, follow Apache-2.0 §4(b): include a notice in modified files indicating they have been changed. This is normal practice for most forks already.
- **Your existing distributions of older Alcatrazer versions are unaffected.** Versions released before this change were licensed under MIT and remain so. The new licence applies to releases from this version onward.

## Why we chose Apache-2.0 specifically

Two honest reasons:

1. **Explicit patent and trademark guarantees** for a security tool whose value depends on integrity and trust. The protections we have walked through above are not theoretical for a project in this space.
2. **No meaningful downside** for our users. The differences that matter are improvements; the differences that go the other way (GPLv2-only compatibility) are practically irrelevant for Alcatrazer's design.

## Further reading

If you want to verify any of the above against the source texts:

- [`license_dependencies_and_usage.md`](./license_dependencies_and_usage.md) — companion document showing every dependency Alcatrazer has, its licence, and why the graph is compatible with Apache-2.0.
- [Apache License 2.0 — full text](https://www.apache.org/licenses/LICENSE-2.0)
- [MIT License — Open Source Initiative](https://opensource.org/license/mit)
- [GNU — Various Licenses and Comments on Them](https://www.gnu.org/licenses/license-list.en.html) — the canonical compatibility matrix
- [Apache Software Foundation FAQ on Apache-2.0](https://www.apache.org/foundation/license-faq.html)
- [SPDX Licence List](https://spdx.org/licenses/) — SPDX identifiers for both licences

## Questions or concerns

Open a GitHub issue. We mean it: clarifying the licence change for one person almost always means clarifying it for many. If something below the legal-jargon line is unclear, that is a documentation bug we want to fix.