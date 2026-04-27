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
"""

SUPPORTED_LANGUAGES: dict[str, dict] = {
    "python": {
        "default_manager": "pip",
        "managers": ("pip", "uv", "poetry", "pipenv"),
        "version_check": "python --version",
    },
    "node": {
        "default_manager": "npm",
        "managers": ("npm", "pnpm", "yarn"),
        "version_check": "node --version",
    },
    "rust": {
        "default_manager": "cargo",
        "managers": ("cargo",),
        # `rustc` is the compiler binary; `rust` is not a command.
        "version_check": "rustc --version",
    },
    "go": {
        "default_manager": "go",
        "managers": ("go",),
        # `go version` is a subcommand — go's CLI does not accept --version.
        "version_check": "go version",
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
    },
}
