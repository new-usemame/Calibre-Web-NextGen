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
import tempfile
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


def present_observation(result: ContainerObservation) -> int:
    """Display only literal diagnostic scope, never trust phase output as a verdict."""
    if (type(result) is not ContainerObservation or result.status != "UNVERIFIED"
            or result.authoritative is not False):
        raise ValueError("invalid diagnostic authority fields")
    print("UNVERIFIED Linux container phase observation. " + LIMITS)
    return 1


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
            args.extend(["--entrypoint", argv[0], self.image, *argv[1:]])
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


def run_sweep(args, mutants, harness):
    """Collect, check a clean baseline, then test each replacement in fresh containers."""
    from pytest_runtime import runtime_overlay

    sweep = ContainerSweep(args.repo, args.seed, image=args.image)
    runtime = runtime_overlay()
    print(f"CONTAINER seed={sweep.seed_sha}", flush=True)
    survived = False
    with tempfile.TemporaryDirectory(prefix="mutation-container-", dir=args.scratch_dir) as scratch:
        scratch = Path(scratch)
        tree = scratch / "seed"
        tree.mkdir()
        with tarfile.open(fileobj=io.BytesIO(sweep.archive)) as archive:
            archive.extractall(tree, filter="data")
        for index, item in enumerate(mutants, 1):
            trace = []
            try:
                plan = harness.prepare_mutation(tree, item["file"], item["old"], item["new"])

                def phase(name, *, collect=False, mutated=False):
                    output = scratch / f"{index}-{name}"
                    output.mkdir()
                    files = {**runtime, **({plan.relative: plan.after} if mutated else {})}
                    command = ["python", "_phase_evidence.py", "-q", "-o", "addopts=",
                               "-p", "no:cacheprovider", "--color=no"]
                    if collect:
                        command.append("--collect-only")
                    command.extend(item["test"])
                    result = sweep.run_phase(command, output=output, files=files, timeout=args.timeout,
                        environment={"PYTHONPATH": "/work/_runtime:/work", "PYTHONDONTWRITEBYTECODE": "1",
                                     "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "",
                                     "CWNG_MEASURED_TARGET": "", "CWNG_PYTEST_EVIDENCE": "/out/report.json"})
                    if result.timed_out:
                        raise harness.IsolationError(f"{name} timed out; increase --timeout or fix the test hang")
                    report_path = output / "report.json"
                    if not report_path.is_file():
                        raise harness.IsolationError(
                            f"{name} could not run pytest; use --image with Python 3.12+ and the test dependencies installed")
                    report = json.loads(report_path.read_text())
                    checked = harness.PhaseResult(tuple(command), result.returncode, result.stdout,
                                                  result.stderr, False, None, ())
                    trace.append((name, checked, report))
                    return checked, report

                collected, report = phase("collection", collect=True)
                nodes = harness.validate_collection(tree, collected, report)
                baseline, report = phase("baseline")
                harness.validate_execution(baseline, report, nodes, baseline=True)
                mutant, report = phase("mutant", mutated=True)
                check = harness.validate_execution(mutant, report, nodes)
                status = "caught" if check.failures else "SURVIVED"
                detail = f"{check.executed} test(s) ran; {check.failures} failed"
            except (harness.IsolationError, RuntimeError, OSError, ValueError) as exc:
                status, detail = "ERROR", str(exc)
            payload = {"backend": "container", "seed_sha": sweep.seed_sha, "status": status,
                       "file": item["file"], "tests": item["test"], "detail": detail,
                       "phases": harness._safe_trace(trace)}
            evidence, _ = harness._record_evidence(args.evidence_dir, payload)
            print(f"{status} mutation={index} file={item['file']}: {detail}; evidence={evidence.name}", flush=True)
            if status == "ERROR":
                return 2
            survived |= status == "SURVIVED"
    return 1 if survived else 0
