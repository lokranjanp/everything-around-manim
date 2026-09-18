from __future__ import annotations

import io
import tarfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from manim_contracts.models import SceneManifest

from .settings import RenderSettings, get_render_settings


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
    def __init__(self, settings: RenderSettings):
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


class KubernetesRunner(Runner):
    def __init__(self, settings: RenderSettings):
        from kubernetes import client, config

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.client = client
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()
        self.settings = settings

    def run(
        self,
        source: bytes,
        manifest: SceneManifest,
        assets: dict[str, bytes],
        cancel_check: Callable[[], bool] | None = None,
    ) -> RunnerResult:
        run_id = f"render-{uuid4().hex[:16]}"
        work = self.settings.render_shared_root / run_id
        input_path, output_path = work / "input", work / "output"
        input_path.mkdir(parents=True, mode=0o755)
        output_path.mkdir(mode=0o777)
        (input_path / "scene.py").write_bytes(source)
        for relative_path, data in assets.items():
            destination = input_path / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        profile = manifest.profile
        c = self.client
        resources = c.V1ResourceRequirements(
            requests={"cpu": "2", "memory": "4Gi"},
            limits={"cpu": "4" if profile.quality == "final" else "2",
                    "memory": "8Gi" if profile.quality == "final" else "4Gi"},
        )
        security = c.V1SecurityContext(
            run_as_non_root=True, run_as_user=1000, run_as_group=1000,
            allow_privilege_escalation=False, read_only_root_filesystem=True,
            capabilities=c.V1Capabilities(drop=["ALL"]),
        )
        container = c.V1Container(
            name="runtime", image=self.settings.render_image, image_pull_policy="IfNotPresent",
            env=[
                c.V1EnvVar(name="SCENE_CLASS", value=manifest.scene_class),
                c.V1EnvVar(name="QUALITY", value=profile.quality),
                c.V1EnvVar(name="WIDTH", value=str(profile.width)),
                c.V1EnvVar(name="HEIGHT", value=str(profile.height)),
                c.V1EnvVar(name="FPS", value=str(profile.fps)),
                c.V1EnvVar(name="INPUT_DIR", value=f"/work/{run_id}/input"),
                c.V1EnvVar(name="OUTPUT_DIR", value=f"/work/{run_id}/output"),
            ],
            resources=resources, security_context=security,
            volume_mounts=[c.V1VolumeMount(name="work", mount_path="/work")],
        )
        pod = c.V1PodTemplateSpec(
            metadata=c.V1ObjectMeta(labels={"app": "manim-runtime", "render-job": run_id}),
            spec=c.V1PodSpec(
                restart_policy="Never", automount_service_account_token=False,
                security_context=c.V1PodSecurityContext(run_as_non_root=True, fs_group=1000),
                containers=[container],
                volumes=[c.V1Volume(
                    name="work", persistent_volume_claim=c.V1PersistentVolumeClaimVolumeSource(
                        claim_name=self.settings.kubernetes_work_pvc
                    )
                )],
            ),
        )
        job = c.V1Job(
            metadata=c.V1ObjectMeta(name=run_id),
            spec=c.V1JobSpec(
                template=pod, backoff_limit=0,
                active_deadline_seconds=900 if profile.quality == "final" else 300,
                ttl_seconds_after_finished=300,
            ),
        )
        self.batch.create_namespaced_job(self.settings.kubernetes_namespace, job)
        deadline = time.monotonic() + (900 if profile.quality == "final" else 300)
        logs = ""
        while time.monotonic() < deadline:
            if cancel_check and cancel_check():
                self.batch.delete_namespaced_job(
                    run_id, self.settings.kubernetes_namespace,
                    propagation_policy="Foreground",
                )
                raise RenderCancelled("render cancelled")
            status = self.batch.read_namespaced_job_status(run_id, self.settings.kubernetes_namespace).status
            if status.succeeded:
                break
            if status.failed:
                raise RuntimeError("Kubernetes render Job failed")
            time.sleep(2)
        else:
            raise TimeoutError("Kubernetes render Job timed out")
        pods = self.core.list_namespaced_pod(
            self.settings.kubernetes_namespace, label_selector=f"job-name={run_id}"
        ).items
        if pods:
            logs = self.core.read_namespaced_pod_log(pods[0].metadata.name, self.settings.kubernetes_namespace)
        mp4 = (output_path / "final.mp4").read_bytes()
        png = (output_path / "final.png").read_bytes()
        contact = output_path / "contact-sheet.png"
        return RunnerResult(mp4=mp4, png=png, contact_sheet=contact.read_bytes() if contact.exists() else png, logs=logs[-20_000:])


def get_runner(settings: RenderSettings | None = None) -> Runner:
    settings = settings or get_render_settings()
    if settings.render_runner == "fake":
        return FakeRunner()
    if settings.render_runner == "docker":
        return DockerRunner(settings)
    if settings.render_runner == "kubernetes":
        return KubernetesRunner(settings)
    raise ValueError(f"unsupported RENDER_RUNNER: {settings.render_runner}")
