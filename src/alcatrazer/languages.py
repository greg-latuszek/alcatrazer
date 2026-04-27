"""Language metadata — a whitelist shared across alcatrazer modules.

`SUPPORTED_LANGUAGES` is consumed by the wizard (`alcatrazer.start`) for menu
display + input validation, and by sandbox adapters (`alcatrazer.docker_prison`,
future ones) for the per-language version-check commands embedded in the
generated container recipe. Adding a new language requires picking its
default manager, allowed managers, and version-check command explicitly.

Per-entry fields:

- ``default_manager`` (str) — what the wizard fills in when the user accepts
  the default manager prompt.
- ``managers`` (tuple[str, ...]) — every manager value the wizard accepts.
- ``version_check`` (str) — shell command run in the Dockerfile verify block.
- ``required_os_packages`` (tuple[str, ...], optional) — apt packages whose
  *absence at runtime* breaks the language. Unioned with user-declared
  ``[os].packages`` and installed at image build time. Optional because most
  languages run on what Ubuntu 24.04's minimal base provides; .NET is the
  first that needs anything (libicu, for ICU/globalization). Build-time apt
  is the right phase because the Alcatraz agent user has no runtime sudo.
- ``version_tip`` (str, optional) — printed once by the wizard before the
  ``Version for <lang>:`` prompt, when the language has version-string
  conventions a typical user wouldn't guess (Java's distribution prefixes
  like ``corretto-21`` are the first such case). Languages without it get
  the bare prompt — the field is purely additive.
"""

SUPPORTED_LANGUAGES: dict[str, dict] = {
    "python": {
        "default_manager": "pip",
        "managers": ("pip", "uv", "poetry", "pipenv"),
        "version_check": "python --version",
        "version_tip": (
            'Version examples: 3.11, 3.12, 3.13. Pin a concrete release; "latest" is rejected.'
        ),
    },
    "node": {
        "default_manager": "npm",
        "managers": ("npm", "pnpm", "yarn"),
        "version_check": "node --version",
        "version_tip": (
            "Version examples: 18, 20, 22. "
            "LTS lines (even-numbered) are recommended for production."
        ),
    },
    "rust": {
        "default_manager": "cargo",
        "managers": ("cargo",),
        # `rustc` is the compiler binary; `rust` is not a command.
        "version_check": "rustc --version",
        "version_tip": "Version examples: 1.75, 1.83. Pin a concrete release.",
    },
    "go": {
        "default_manager": "go",
        "managers": ("go",),
        # `go version` is a subcommand — go's CLI does not accept --version.
        "version_check": "go version",
        "version_tip": "Version examples: 1.22, 1.23. Pin a concrete release.",
    },
    "dotnet": {
        "default_manager": "dotnet",
        # The .NET CLI is the only canonical toolchain — `dotnet add package`
        # talks to NuGet under the hood. Single-element tuple, same shape as
        # rust/go (the wizard skips the manager prompt for these).
        "managers": ("dotnet",),
        "version_check": "dotnet --version",
        # libicu74 fixes the otherwise-fatal "Couldn't find a valid ICU
        # package" startup crash on Ubuntu 24.04. Empirically verified inside
        # a fresh Alcatraz container.
        "required_os_packages": ("libicu74",),
        "version_tip": (
            "Version examples: 8.0.404 (LTS), 10.0.100 (current LTS). Pin to major.minor.patch."
        ),
    },
    "java": {
        "default_manager": "maven",
        # Maven and Gradle both have core/aqua mise plugins that auto-install
        # on first use; ant and sbt are asdf-only and deferred (they'd risk
        # build-time failures without a `requires_plugin_install` flag we
        # deliberately avoided for dotnet).
        "managers": ("maven", "gradle"),
        # `2>&1` is load-bearing: java prints `-version` output to stderr,
        # and the verify block's chained `&&` only captures stdout in the
        # docker-build log. Without the redirect, the version line is lost.
        "version_check": "java -version 2>&1",
        # JDK binary distributions (Temurin by default in mise) link only
        # against Ubuntu 24.04's libc / libstdc++. Verified empirically in
        # a fresh Alcatraz: `mise use --global java@21` installs OpenJDK
        # 21.0.2 cleanly, no apt extras needed.
        "required_os_packages": (),
        # Java is the first language with multiple shipped distributions
        # (Temurin / Corretto / Zulu / Liberica / GraalVM) reachable via
        # the same mise key. The wizard surfaces the prefix syntax mid-
        # prompt so distribution-conscious users don't miss the option.
        # Phase 1.2.3 rewrote the leading words to match the rest of the
        # language tips ("Version examples: …" prefix).
        "version_tip": (
            "Version examples: 17, 21. "
            "Defaults to Eclipse Temurin. Prefix for alternatives, "
            'e.g. "corretto-21", "zulu-21", "graalvm-21". '
            "See README for the full list."
        ),
    },
}
