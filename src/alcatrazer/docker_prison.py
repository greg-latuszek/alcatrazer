"""DockerPrison — the docker-backed Alcatraz adapter.

Currently the only Alcatraz implementation. Shells out to `docker build / run /
start / stop / ps / exec / rm` subprocesses for every sandbox operation. Image
tag and container name default to generic values that do not leak alcatrazer
branding into host-visible Docker metadata (Principle 2).

All methods are skeletons for now; each is filled in by the installer step
that first needs it:

- `build` — Step 3i
- `start`, `exec` — Step 3k
- `image_exists`, `is_running`, `remove` — Step 4 (subsequent-run detection)
- `stop` — Step 5
"""

from pathlib import Path

from alcatrazer.alcatraz import Alcatraz


class DockerPrison(Alcatraz):
    """Docker-backed sandbox adapter."""

    def __init__(
        self,
        project_dir: Path,
        image_tag: str = "alcatraz-workspace:local",
        container_name: str = "workspace",
    ):
        super().__init__(project_dir)
        self.image_tag = image_tag
        self.container_name = container_name

    def build(self) -> None:
        raise NotImplementedError("DockerPrison.build lands in Step 3i")

    def image_exists(self) -> bool:
        raise NotImplementedError("DockerPrison.image_exists lands in Step 4")

    def start(self) -> None:
        raise NotImplementedError("DockerPrison.start lands in Step 3k")

    def stop(self) -> None:
        raise NotImplementedError("DockerPrison.stop lands in Step 5")

    def is_running(self) -> bool:
        raise NotImplementedError("DockerPrison.is_running lands in Step 4")

    def exec(self, command: list[str]) -> int:
        raise NotImplementedError("DockerPrison.exec lands in Step 3k")

    def remove(self) -> None:
        raise NotImplementedError("DockerPrison.remove lands in Step 4")
