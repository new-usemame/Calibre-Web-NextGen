# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproduce unresolved measurement defects; always exit nonzero (Mac/APFS only)."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HARNESS = Path(__file__).with_name("mutate.py").resolve()


def command(argv, cwd, env=None):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


def git(repo, *args):
    # A throwaway fixture repo has no business authoring as a real person. The synthetic
    # identity also keeps the machine's identity hook out of the way: that hook checks a real
    # identity against its intended origin, and these repos deliberately have no origin.
    result = command(["git", "-c", "user.name=Mutation Tests",
        "-c", "user.email=mutation-tests.invalid", *args], repo)
    if result.returncode:
        # Include the reason. Swallowing stderr here turns any fixture problem into a
        # four-probe investigation, which is exactly what it cost once.
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise RuntimeError("fixture Git command failed: git %s -> %s" % (
            " ".join(args), " | ".join(detail[-3:]) or "no output"))
    return result.stdout.strip()


def fixture(root, mode):
    repo = root / mode
    (repo / "cps").mkdir(parents=True)
    (repo / "cps/__init__.py").write_text("# synthetic package\n")
    (repo / "cps/main.py").write_text("def main(): raise RuntimeError('must not start')\n")
    (repo / ".gitignore").write_text("*.pyc\n__pycache__/\n")
    (repo / "victim.py").write_text("VALUE = 1\n")
    body = "import victim\ndef test_value(): assert victim.VALUE == 1\n"
    if mode in ("absent_control", "frame_forge"):
        body = "from pathlib import Path\n"
        if mode == "frame_forge":
            body += "exec(compile('SEEN = 1', str(Path('victim.py').resolve()), 'exec'), {})\n"
        body += "def test_value(): assert True\n"
    if mode == "startup_rewrite":
        (repo / "sitecustomize.py").write_text(
            "from pathlib import Path\np = Path('victim.py')\n"
            "if p.exists(): p.write_text('VALUE = 1\\n')\n")
    if mode in ("meta_transform", "loader_transform"):
        # Both read the actual intended source, but execute different code.
        setup = """import hashlib, importlib.abc, importlib.machinery, importlib.util, os, sys
from pathlib import Path
def replacement(source, filename):
    Path(os.environ['LOAD_WITNESS']).write_text(hashlib.sha256(source).hexdigest())
    return compile('VALUE = 1', filename, 'exec')
"""
        if mode == "meta_transform":
            setup += """class Loader(importlib.abc.Loader):
    def create_module(self, spec): return None
    def exec_module(self, module):
        path = Path('victim.py').resolve()
        source = path.read_bytes()
        exec(replacement(source, str(path)), module.__dict__)
class Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == 'victim':
            return importlib.util.spec_from_loader(fullname, Loader())
sys.meta_path.insert(0, Finder())
"""
        else:
            setup += """original = importlib.machinery.SourceFileLoader.source_to_code
def changed(self, data, path, *, _optimize=-1):
    if self.name == 'victim':
        return replacement(data, path)
    return original(self, data, path, _optimize=_optimize)
importlib.machinery.SourceFileLoader.source_to_code = changed
"""
        body = setup + body
    (repo / "test_probe.py").write_text(body)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "measurement reproduction")
    return repo


def main():
    with tempfile.TemporaryDirectory(prefix="measurement-audit-") as temp:
        root = Path(temp)
        for mode in ("clean_control", "startup_rewrite", "absent_control", "frame_forge",
                     "meta_transform", "loader_transform"):
            repo = fixture(root, mode)
            witness = root / (mode + "-loaded-hash")
            env = {**os.environ, "PYTEST_ADDOPTS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                   "PYTHONDONTWRITEBYTECODE": "1", "LOAD_WITNESS": str(witness)}
            if mode == "clean_control":
                (repo / "victim.py").write_text("VALUE = 2\n")
                direct = command([sys.executable, "-m", "pytest", "test_probe.py", "-q",
                    "-o", "addopts=", "-p", "no:cacheprovider", "--color=no"], repo,
                    {**env, "PYTHONPATH": str(repo)})
                print(f"CONTROL direct-mutant-exit={direct.returncode} (Mac/APFS only)", flush=True)
                assert direct.returncode == 1
                git(repo, "restore", "victim.py")
            evidence = root / (mode + "-evidence")
            proc = command([sys.executable, str(HARNESS), "--repo", str(repo), "--seed", "HEAD",
                "--file", "victim.py", "--old", "VALUE = 1", "--new", "VALUE = 2",
                "--test", "test_probe.py", "--evidence-dir", str(evidence)], repo, env)
            files = list(evidence.glob("*.json"))
            if len(files) != 1:
                raise RuntimeError("reproduction did not produce one diagnostic")
            payload = json.loads(files[0].read_text())
            codes = [p["returncode"] for p in payload["phases"]]
            print(f"UNVERIFIED {mode}: signal={payload['signal']} phases={codes} exit={proc.returncode} (Mac/APFS only)", flush=True)
            if mode in ("meta_transform", "loader_transform"):
                matches = witness.read_text() == hashlib.sha256(b"VALUE = 2\n").hexdigest()
                print(f"LOADER {mode}: read-intended-mutant={matches}; test-observed-VALUE=1 (Mac/APFS only)", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
