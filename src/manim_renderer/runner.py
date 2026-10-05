from __future__ import annotations

import io
import tarfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from manim_agent.settings import Settings, get_settings
from manim_contracts.models import SceneManifest


@dataclass
class RunnerResult:
    mp4: bytes
    png: bytes
    contact_sheet: bytes
    logs: str


class RenderCancelled(RuntimeError):
    pass


class Runner(ABC):
    @abstractmethod
    def run(
        self,
        source: bytes,
        manifest: SceneManifest,
        assets: dict[str, bytes],
        cancel_check: Callable[[], bool] | None = None,
    ) -> RunnerResult: ...


class FakeRunner(Runner):
    # Valid 1x1 transparent PNG; fake MP4 is deliberately only a test fixture.
    PNG = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d49444154789c63606060f80f0001040100b51c0c020000000049454e44ae426082"
    )

    def run(
        self,
        source: bytes,
        manifest: SceneManifest,
        assets: dict[str, bytes],
        cancel_check: Callable[[], bool] | None = None,
    ) -> RunnerResult:
        if cancel_check and cancel_check():
            raise RenderCancelled("render cancelled")
        return RunnerResult(
            mp4=b"fake-mp4-for-contract-tests",
            png=self.PNG,
            contact_sheet=self.PNG,
            logs=f"fake render completed for {manifest.scene_class}",
        )


class DockerRunner(Runner):
    def __init__(self, settings: Settings):
        import docker

        self.client = docker.from_env()
        self.settings = settings

    def run(
        self,
        source: bytes,
        manifest: SceneManifest,
        assets: dict[str, bytes],
        cancel_check: Callable[[], bool] | None = None,
    ) -> RunnerResult:
        profile = manifest.profile
        environment = {
            "SCENE_CLASS": manifest.scene_class,
            "QUALITY": profile.quality,
            "WIDTH": str(profile.width),
            "HEIGHT": str(profile.height),
            "FPS": str(profile.fps),
        }
        input_volume = self.client.volumes.create()
        output_volume = self.client.volumes.create()
        container = None
        try:
            container = self.client.containers.create(
                self.settings.render_image,
                network_disabled=True,
                read_only=True,
                user="1000:1000",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                pids_limit=256,
                mem_limit="8g" if profile.quality == "final" else "4g",
                nano_cpus=4_000_000_000 if profile.quality == "final" else 2_000_000_000,
                environment=environment,
                volumes={
                    input_volume.name: {"bind": "/input", "mode": "rw"},
                    output_volume.name: {"bind": "/output", "mode": "rw"},
                },
                tmpfs={"/tmp": "rw,noexec,nosuid,size=1g", "/home/renderer": "rw,nosuid,size=256m"},
            )
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as tar:
                info = tarfile.TarInfo("scene.py")
                info.size = len(source)
                info.mode = 0o444
                tar.addfile(info, io.BytesIO(source))
                for relative_path, data in assets.items():
                    info = tarfile.TarInfo(relative_path)
                    info.size = len(data)
                    info.mode = 0o444
                    tar.addfile(info, io.BytesIO(data))
            if not container.put_archive("/input", archive.getvalue()):
                raise RuntimeError("could not stage source in render container")
            container.start()
            deadline = time.monotonic() + (900 if profile.quality == "final" else 300)
            while time.monotonic() < deadline:
                container.reload()
                if container.status in {"exited", "dead"}:
                    break
                if cancel_check and cancel_check():
                    container.kill()
                    raise RenderCancelled("render cancelled")
                time.sleep(1)
            else:
                container.kill()
                raise TimeoutError("isolated render container timed out")
            result = container.wait(timeout=10)
            logs = container.logs(stdout=True, stderr=True).decode(errors="replace")
            if result["StatusCode"] != 0:
                raise RuntimeError(f"runtime exited {result['StatusCode']}: {logs[-4000:]}")
            stream, _ = container.get_archive("/output")
            output_tar = io.BytesIO(b"".join(stream))
            files: dict[str, bytes] = {}
            with tarfile.open(fileobj=output_tar, mode="r:") as tar:
                for member in tar.getmembers():
                    if member.isfile() and Path(member.name).name in {
                        "final.mp4", "final.png", "contact-sheet.png"
                    }:
                        files[Path(member.name).name] = tar.extractfile(member).read()
            mp4, png = files["final.mp4"], files["final.png"]
            return RunnerResult(
                mp4=mp4, png=png, contact_sheet=files.get("contact-sheet.png", png),
                logs=logs[-20_000:],
            )
        except RenderCancelled:
            raise
        except Exception as exc:
            raise RuntimeError(f"isolated render container failed: {exc}") from exc
        finally:
            if container is not None:
                container.remove(force=True)
            input_volume.remove(force=True)
            output_volume.remove(force=True)


def get_runner(settings: Settings | None = None) -> Runner:
    settings = settings or get_settings()
    if settings.render_runner == "fake":
        return FakeRunner()
    if settings.render_runner == "docker":
        return DockerRunner(settings)
    raise ValueError(f"unsupported RENDER_RUNNER: {settings.render_runner}")
