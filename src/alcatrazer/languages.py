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
- ``version_tip`` (str) — printed once by the wizard before the
  ``Version for <lang>:`` prompt, and emitted as a ``# ``-prefixed comment
  block above each ``version =`` line in the generated TOML. Required
  since Phase 1.2.3 (every entry declares one).
- ``bundled_managers`` (tuple[str, ...]) — manager names that ship with
  the runtime and don't need a separate ``mise use --global`` install.
  Empty tuple is valid (e.g. java's `()` — Maven and Gradle are both
  separate installs). Phase 1.2.4: separates the "default manager" UI
  hint from the "what mise actually installs" decision; previously
  conflated, which broke for Java where ``maven`` is the default but
  not bundled with the JDK.
- ``manager_tip`` (str) — printed once by the wizard before the manager
  prompt (multi-manager case) and emitted as a ``# ``-prefixed comment
  block above each ``manager =`` line in the generated TOML. Required
  since Phase 1.2.4 — symmetric with ``version_tip``.
"""

SUPPORTED_LANGUAGES: dict[str, dict] = {
    "python": {
        "default_manager": "pip",
        "managers": ("pip", "uv", "poetry", "pipenv"),
        # pip ships with CPython; mise installs Python so pip arrives for
        # free. uv / poetry / pipenv are separate installs.
        "bundled_managers": ("pip",),
        "version_check": "python --version",
        "version_tip": (
            'Version examples: 3.11, 3.12, 3.13. Pin a concrete release; "latest" is rejected.'
        ),
        "manager_tip": (
            "Default: pip (bundled with Python). Alternatives: uv (fast, "
            "Rust-based), poetry, pipenv. Non-default options install via mise."
        ),
    },
    "node": {
        "default_manager": "npm",
        "managers": ("npm", "pnpm", "yarn"),
        # npm ships with Node; pnpm / yarn install separately.
        "bundled_managers": ("npm",),
        "version_check": "node --version",
        "version_tip": (
            "Version examples: 18, 20, 22. "
            "LTS lines (even-numbered) are recommended for production."
        ),
        "manager_tip": (
            "Default: npm (bundled with Node). Alternatives: pnpm "
            "(fast, disk-efficient), yarn. Non-default options install via mise."
        ),
    },
    "rust": {
        "default_manager": "cargo",
        "managers": ("cargo",),
        # cargo ships with the Rust toolchain.
        "bundled_managers": ("cargo",),
        # `rustc` is the compiler binary; `rust` is not a command.
        "version_check": "rustc --version",
        "version_tip": "Version examples: 1.75, 1.83. Pin a concrete release.",
        "manager_tip": (
            "Default: cargo (bundled with the Rust toolchain). Single canonical manager."
        ),
    },
    "go": {
        "default_manager": "go",
        "managers": ("go",),
        # `go` IS the runtime; bundled by tautology.
        "bundled_managers": ("go",),
        # `go version` is a subcommand — go's CLI does not accept --version.
        "version_check": "go version",
        "version_tip": "Version examples: 1.22, 1.23. Pin a concrete release.",
        "manager_tip": "Default: go (the manager is the runtime). Single canonical manager.",
    },
    "dotnet": {
        "default_manager": "dotnet",
        # The .NET CLI is the only canonical toolchain — `dotnet add package`
        # talks to NuGet under the hood. Single-element tuple, same shape as
        # rust/go (the wizard skips the manager prompt for these).
        "managers": ("dotnet",),
        # `dotnet` IS the runtime; bundled by tautology.
        "bundled_managers": ("dotnet",),
        "version_check": "dotnet --version",
        # libicu74 fixes the otherwise-fatal "Couldn't find a valid ICU
        # package" startup crash on Ubuntu 24.04. Empirically verified inside
        # a fresh Alcatraz container.
        "required_os_packages": ("libicu74",),
        "version_tip": (
            "Version examples: 8.0.404 (LTS), 10.0.100 (current LTS). Pin to major.minor.patch."
        ),
        "manager_tip": (
            "Default: dotnet (the SDK CLI; uses NuGet under the hood). Single canonical manager."
        ),
    },
    "java": {
        "default_manager": "maven",
        # Maven and Gradle both have core/aqua mise plugins that auto-install
        # on first use; ant and sbt are asdf-only and deferred (they'd risk
        # build-time failures without a `requires_plugin_install` flag we
        # deliberately avoided for dotnet).
        "managers": ("maven", "gradle"),
        # NOTHING is bundled with the JDK — both maven and gradle are
        # separate mise installs. This is the heart of the bug Phase 1.2.4
        # fixes: previously, accepting Java's default `[maven]` left Maven
        # uninstalled because `_render_mise_uses` only emitted the install
        # line for non-default managers. With `bundled_managers = ()`, ANY
        # picked manager (default or override) gets installed.
        "bundled_managers": (),
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
        "version_tip": (
            "Version examples: 17, 21. "
            "Defaults to Eclipse Temurin. Prefix for alternatives, "
            'e.g. "corretto-21", "zulu-21", "graalvm-21". '
            "See README for the full list."
        ),
        "manager_tip": "Default: maven. Alternative: gradle. Both auto-install via mise.",
    },
}
