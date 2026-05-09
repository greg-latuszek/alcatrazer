# Project conventions for Claude

This file contains project-specific instructions that apply to every Claude
conversation in this repo. Keep entries short, declarative, and focused on
constraints that aren't obvious from the codebase itself.

## README.md links

When editing `README.md`, all links to files in this repository **must** be
absolute GitHub URLs of the form
`[text](https://github.com/greg-latuszek/alcatrazer/blob/main/<path>)`, never relative
paths. PyPI's README renderer does not resolve relative links, so they appear
broken on the project page (this was originally fixed in v0.0.2). Same-document
anchor links like `[text](#section)` are fine — they render correctly on PyPI.

This rule applies only to `README.md`. Other markdown files (CHANGELOG, `docs/`)
are read on GitHub directly and may use relative links.