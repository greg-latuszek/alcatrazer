"""Language metadata — a whitelist shared across alcatrazer modules.

`SUPPORTED_LANGUAGES` is consumed by the wizard (`alcatrazer.start`) for menu
display + input validation, and by sandbox adapters (`alcatrazer.docker_prison`,
future ones) for the per-language version-check commands embedded in the
generated container recipe. Adding a new language requires picking its
default manager, allowed managers, and version-check command explicitly.
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
}
