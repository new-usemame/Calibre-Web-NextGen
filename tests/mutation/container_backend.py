# SPDX-License-Identifier: GPL-3.0-or-later
"""Linux PID-namespace phase boundary; execution provenance is NOT established.

Each phase receives a Git archive of one pinned commit, never the source checkout
or its Git directory. Only a dedicated disposable output directory is shared.
Removing the container contains descendants, including setsid/env -i children.
It does not contain externally delegated work: databases, network services,
shared ports, remote service managers, or daemons outside the container. This is
not hermeticity. A contained process may still execute substituted Python code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import io
import json
from pathlib import Path
import subprocess
import tarfile
import uuid


LABEL = "org.cwng.mutation-phase"
LIMITS = ("Outside containment: externally delegated work in databases, network "
          "services, shared ports, remote service managers, and daemons outside "
          "the container. Not hermetic. Execution provenance is UNVERIFIED.")


@dataclass(frozen=True, slots=True)
class ContainerObservation:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    container_id: str
    seed_sha: str
    status: str = field(default="UNVERIFIED", init=False)
    authoritative: bool = field(default=False, init=False)


def _docker(*args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, timeout=30, **kwargs)


def _checked(proc):
    if proc.returncode:
        # Do not relay paths or arbitrary damaged-code output in host exceptions.
        raise RuntimeError("container boundary operation failed")
    return proc.stdout


class ContainerSweep:
    """Copy the pinned seed into a fresh, unprivileged container for every phase.

    The caller supplies an empty disposable output directory for each phase.
    No socket, host PID namespace, credentials, or caller environment is passed.
    Docker daemon/kernel correctness and a trusted image are prerequisites.
    This class deliberately cannot issue a mutation verdict.
    """

    def __init__(self, repo: Path, seed: str, *, image="python:3.12"):
        self.repo = repo.resolve()
        self.seed_sha = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "--verify", f"{seed}^{{commit}}"],
            check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        # Resolve the tag once; later tag changes cannot change phase images.
        self.image = _checked(_docker("image", "inspect", image, "--format", "{{.Id}}" )).decode().strip()
        self.archive = subprocess.run(
            ["git", "-C", str(self.repo), "archive", "--format=tar", self.seed_sha],
            check=True, capture_output=True, timeout=30).stdout
        self.failed = False

    def run_phase(self, argv, *, output: Path, timeout=30, files=None, environment=None):
        if self.failed:
            raise RuntimeError("sweep cannot run another phase after an error")
        output = output.resolve()
        if not output.is_dir() or any(output.iterdir()):
            raise ValueError("phase output must be an empty disposable directory")
        token = uuid.uuid4().hex
        name = "cwng-mutation-" + token
        cid = None
        try:
            args = ["create", "--pull=never", "--name", name, "--label", f"{LABEL}={token}",
                    "--network=none", "--cgroupns=private", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges", "--pids-limit=64",
                    "--memory=256m", "--cpus=1", "--workdir=/work",
                    "--mount", f"type=bind,src={output},dst=/out"]
            for key, value in (environment or {}).items():
                args.extend(["--env", f"{key}={value}"])
            args.extend([self.image, *argv])
            cid = _checked(_docker(*args)).decode().strip()
            _checked(_docker("cp", "-", cid + ":/work", input=self.archive))
            if files:
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    for relative, data in files.items():
                        path = Path(relative)
                        if path.is_absolute() or ".." in path.parts:
                            raise ValueError("phase overlay must be repository-relative")
                        item = tarfile.TarInfo(relative)
                        item.size = len(data)
                        archive.addfile(item, io.BytesIO(data))
                _checked(_docker("cp", "-", cid + ":/work", input=stream.getvalue()))
            _checked(_docker("start", cid))
            timed_out = False
            try:
                waited = subprocess.run(["docker", "wait", cid], capture_output=True,
                                        text=True, timeout=timeout)
                code = int(_checked(waited).strip())
            except subprocess.TimeoutExpired:
                timed_out, code = True, None
            logs = _docker("logs", cid)
            _checked(logs)
            observation = ContainerObservation(code, logs.stdout.decode(errors="replace"),
                logs.stderr.decode(errors="replace"), timed_out, cid, self.seed_sha)
        except BaseException:
            self.failed = True
            raise
        finally:
            # Also recover a create whose CLI lost its reply. Never remove by a
            # guessed name without checking our unique ownership label first.
            inspected = _docker("container", "inspect", cid or name)
            if inspected.returncode == 0:
                info = json.loads(inspected.stdout)[0]
                if info["Config"]["Labels"].get(LABEL) != token:
                    self.failed = True
                    raise RuntimeError("container ownership mismatch")
                try:
                    _checked(_docker("rm", "-f", info["Id"]))
                    remaining = _checked(_docker("ps", "-aq", "--filter", f"label={LABEL}={token}"))
                    if remaining.strip():
                        raise RuntimeError("phase container remains after removal")
                except BaseException:
                    self.failed = True
                    raise
            elif cid:
                self.failed = True
                raise RuntimeError("cannot establish container removal")
        if timed_out:
            self.failed = True
        return observation
