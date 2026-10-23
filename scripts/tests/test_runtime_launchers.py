"""Offline contract tests for recipe-owned runtime launchers."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON_RUN = ROOT / "templates/profiles/python/bare/recipe/python/bare/run.sh"
GO_RUN = ROOT / "templates/profiles/go/bare/recipe/go/bare/run.sh"
JAVA_RUN = ROOT / "templates/profiles/java/bare/recipe/java/bare/run.sh"


class RuntimeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="runtime launcher ")
        self.root = Path(self.temp.name)
        self.bin = self.root / "fake bin"
        self.bin.mkdir()
        self.trace = self.root / "trace.json"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}/usr/bin:/bin",
            "TRACE": str(self.trace),
        }

    def tearDown(self):
        self.temp.cleanup()

    def executable(self, path: Path, content: str):
        path.write_text(content)
        path.chmod(0o755)

    def test_all_profile_and_supported_recipe_launchers_are_independent_executables(self):
        profiles = sorted((ROOT / "templates/profiles").glob("**/recipe/**/run.sh"))
        recipes = sorted((ROOT / "recipes").glob("*/python/*/run.sh"))
        recipes += sorted((ROOT / "recipes").glob("*/go/*/run.sh"))
        self.assertEqual(len(profiles), 10)
        self.assertEqual(len(recipes), 9)
        canonical = {"python": PYTHON_RUN, "go": GO_RUN, "java": JAVA_RUN}
        for launcher in profiles + recipes:
            language = next(part for part in launcher.parts if part in canonical)
            self.assertTrue(os.access(launcher, os.X_OK), launcher)
            self.assertEqual(launcher.read_bytes(), canonical[language].read_bytes())
            self.assertFalse(launcher.is_symlink())

    def implementation(self, language: str, name: str | None = None) -> Path:
        path = self.root / "implementation with spaces" / (name or language)
        path.mkdir(parents=True)
        source = {"python": PYTHON_RUN, "go": GO_RUN, "java": JAVA_RUN}[language]
        shutil.copy2(source, path / "run.sh")
        return path

    def invoke(self, implementation: Path, *args: str, env=None, timeout=10):
        return subprocess.run(
            [str(implementation / "run.sh"), *args],
            cwd=self.root,
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def invoke_with_pid(self, implementation: Path, *args: str, env=None):
        process = subprocess.Popen(
            [str(implementation / "run.sh"), *args],
            cwd=self.root, env=env or self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        stdout, stderr = process.communicate(timeout=10)
        return process, stdout, stderr

    def fake_python(self, name: str, version: tuple[int, int, int]):
        content = f"""#!{sys.executable}
import json, os, pathlib, shutil, sys
if sys.argv[1:2] == ["-"]:
    try:
        import packaging.specifiers
    except ImportError:
        import pip._vendor.packaging.specifiers
sys.version_info = {version!r}
if pathlib.Path(__file__).parent.name == "bin":
    sys.prefix = str(pathlib.Path(__file__).parent.parent)
if sys.argv[1:2] == ["-"]:
    code = sys.stdin.read()
    sys.argv = sys.argv[1:]
    exec(compile(code, "<compatibility>", "exec"), {{"__name__": "__main__"}})
elif sys.argv[1:3] == ["-m", "venv"]:
    target = pathlib.Path(sys.argv[3])
    (target / "bin").mkdir(parents=True, exist_ok=True)
    (target / "pyvenv.cfg").write_text("home = fake\\n")
    shutil.copy2(__file__, target / "bin/python")
    (target / "bin/python").chmod(0o755)
elif sys.argv[1:3] == ["-m", "pip"]:
    pathlib.Path(os.environ["TRACE"]).write_text(json.dumps({{"pip": sys.argv[3:]}}))
    raise SystemExit(int(os.environ.get("FAKE_PIP_EXIT", "0")))
else:
    pathlib.Path(os.environ["TRACE"]).write_text(json.dumps(
        {{"argv": sys.argv[2:], "pid": os.getpid(),
          "launcher_env": sorted(
              key for key in os.environ if key.startswith("CADENCE_RECIPE_LAUNCHER_"))}}))
    if os.environ.get("FAKE_SIGNAL"):
        os.kill(os.getpid(), int(os.environ["FAKE_SIGNAL"]))
    raise SystemExit(int(os.environ.get("FAKE_APP_EXIT", "0")))
"""
        self.executable(self.bin / name, content)

    def fake_go(self):
        self.executable(
            self.bin / "go",
            f"""#!{sys.executable}
import json, os, pathlib, signal, sys, time
trace = pathlib.Path(os.environ["TRACE"])
if sys.argv[1:3] == ["env", "GOVERSION"]:
    print(os.environ.get("FAKE_GO_VERSION", "go1.24.0"))
    raise SystemExit()
if sys.argv[1:3] == ["mod", "download"]:
    time.sleep(float(os.environ.get("FAKE_SETUP_SLEEP", "0")))
    download_trace = pathlib.Path(os.environ.get("DOWNLOAD_TRACE", os.environ["TRACE"]))
    count = int(download_trace.read_text()) + 1 if download_trace.exists() else 1
    download_trace.write_text(str(count))
    exit_code = int(os.environ.get("FAKE_SETUP_EXIT", "0"))
    if exit_code == 0:
        pathlib.Path(os.environ["GOMODCACHE"]).mkdir(parents=True, exist_ok=True)
        pathlib.Path(os.environ["GOCACHE"]).mkdir(parents=True, exist_ok=True)
    raise SystemExit(exit_code)
trace.write_text(json.dumps({{
    "argv": sys.argv[3:], "pid": os.getpid(),
    "gopath": os.environ.get("GOPATH"),
    "gomodcache": os.environ.get("GOMODCACHE"),
    "gocache": os.environ.get("GOCACHE"),
    "goflags": os.environ.get("GOFLAGS"),
    "launcher_env": sorted(
        key for key in os.environ if key.startswith("CADENCE_RECIPE_LAUNCHER_")),
}}))
if os.environ.get("FAKE_SIGNAL"):
    os.kill(os.getpid(), int(os.environ["FAKE_SIGNAL"]))
raise SystemExit(int(os.environ.get("FAKE_APP_EXIT", "0")))
""",
        )

    def fake_java(self, implementation: Path):
        self.executable(
            self.bin / "java",
            '#!/bin/sh\necho "openjdk version \\"${FAKE_JAVA_VERSION:-17.0.1}\\"" >&2\n',
        )
        self.executable(
            implementation / "gradlew",
            f"""#!{sys.executable}
import json, os, pathlib, signal, sys
trace = pathlib.Path(os.environ["TRACE"])
gradle_home = pathlib.Path(os.environ["GRADLE_USER_HOME"])
if "classes" in sys.argv:
    gradle_home.mkdir(parents=True, exist_ok=True)
    config = gradle_home / "gradle.properties"
    if config.exists():
        print(config.read_text())
    (gradle_home / "prepared").write_text("prepared\\n")
    setup_trace = pathlib.Path(os.environ.get("SETUP_TRACE", os.environ["TRACE"]))
    count = int(setup_trace.read_text()) + 1 if setup_trace.exists() else 1
    setup_trace.write_text(str(count))
    exit_code = int(os.environ.get("FAKE_SETUP_EXIT", "0"))
    if exit_code == 0:
        (gradle_home / "caches").mkdir(parents=True, exist_ok=True)
        (gradle_home / "wrapper/dists").mkdir(parents=True, exist_ok=True)
    raise SystemExit(exit_code)
trace.write_text(json.dumps({{
    "argv": sys.argv[1:], "pid": os.getpid(),
    "gradle_user_home": str(gradle_home),
    "cache_reused": (gradle_home / "prepared").exists(),
    "gradle_opts": os.environ.get("GRADLE_OPTS"),
    "java_opts": os.environ.get("JAVA_OPTS"),
    "java_tool_options": os.environ.get("JAVA_TOOL_OPTIONS"),
    "underscore_java_options": os.environ.get("_JAVA_OPTIONS"),
    "jdk_java_options": os.environ.get("JDK_JAVA_OPTIONS"),
    "launcher_env": sorted(
        key for key in os.environ if key.startswith("CADENCE_RECIPE_LAUNCHER_")),
}}))
if os.environ.get("FAKE_SIGNAL"):
    os.kill(os.getpid(), int(os.environ["FAKE_SIGNAL"]))
raise SystemExit(int(os.environ.get("FAKE_APP_EXIT", "0")))
""",
        )
        (implementation / "settings.gradle.kts").write_text('rootProject.name = "fixture"\n')
        (implementation / "build.gradle.kts").write_text(
            "plugins { application }\n"
            "java { toolchain { languageVersion = JavaLanguageVersion.of(17) } }\n"
        )

    def test_python_312_and_313_are_selected_and_314_is_rejected(self):
        for name, version in (("python3.12", (3, 12, 9)), ("python3.13", (3, 13, 4))):
            with self.subTest(version=version):
                implementation = self.implementation("python", name)
                (implementation / "pyproject.toml").write_text(
                    '[project]\nrequires-python = ">=3.12,<3.14"\n'
                )
                (implementation / "main.py").write_text("")
                self.fake_python(name, version)
                result = self.invoke(implementation, "setup")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Dependencies prepared.", result.stdout)
                shutil.rmtree(implementation.parent)
                (self.bin / name).unlink()

        implementation = self.implementation("python", "python314")
        (implementation / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12,<3.14"\n'
        )
        (implementation / "main.py").write_text("")
        for name in ("python3.13", "python3.12", "python3"):
            self.fake_python(name, (3, 14, 1))
        result = self.invoke(implementation, "setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires-python", result.stderr)

    def test_python_reports_missing_compatible_interpreter(self):
        implementation = self.implementation("python", "python-missing")
        (implementation / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=99"\n'
        )
        (implementation / "main.py").write_text("")
        result = self.invoke(implementation, "setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No installed Python interpreter", result.stderr)

    def test_python_install_failure_leaves_no_ready_marker(self):
        implementation = self.implementation("python", "python-pip-failure")
        (implementation / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12,<3.14"\n'
        )
        (implementation / "main.py").write_text("")
        self.fake_python("python3.13", (3, 13, 4))
        result = self.invoke(
            implementation,
            "setup",
            env={**self.env, "FAKE_PIP_EXIT": "19"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((implementation / ".run-state/ready.v1").exists())
        self.assertFalse((implementation / ".run-state/setup.log.tmp").exists())

    def test_python_rejects_external_symlinked_virtual_environments(self):
        for name, version in (
            ("compatible", (3, 13, 4)),
            ("incompatible", (3, 14, 1)),
        ):
            with self.subTest(name=name):
                implementation = self.implementation("python", f"python-linked-{name}")
                (implementation / "pyproject.toml").write_text(
                    '[project]\nrequires-python = ">=3.12,<3.14"\n'
                )
                (implementation / "main.py").write_text("")
                external = self.root / f"external {name}"
                (external / "bin").mkdir(parents=True)
                self.fake_python(f"external-{name}", version)
                shutil.copy2(self.bin / f"external-{name}", external / "bin/python")
                sentinel = external / "keep"
                sentinel.write_text("untouched\n")
                (implementation / ".venv").symlink_to(
                    external, target_is_directory=True)
                result = self.invoke(implementation, "setup")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Unsafe Python virtual environment", result.stderr)
                self.assertEqual(sentinel.read_text(), "untouched\n")
                self.assertTrue((implementation / ".venv").is_symlink())

    def test_python_rejects_symlinked_virtual_environment_contents(self):
        implementation = self.implementation("python", "python-linked-bin")
        (implementation / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12,<3.14"\n'
        )
        (implementation / "main.py").write_text("")
        external = self.root / "external bin"
        external.mkdir()
        sentinel = external / "keep"
        sentinel.write_text("untouched\n")
        venv = implementation / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = fake\n")
        (venv / "bin").symlink_to(external, target_is_directory=True)
        result = self.invoke(implementation, "setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe Python virtual environment contents", result.stderr)
        self.assertEqual(sentinel.read_text(), "untouched\n")

    def test_python_rejects_symlinked_virtual_environment_package_directory(self):
        implementation = self.implementation("python", "python-linked-lib")
        (implementation / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12,<3.14"\n'
        )
        (implementation / "main.py").write_text("")
        external = self.root / "external packages"
        external.mkdir()
        sentinel = external / "keep"
        sentinel.write_text("untouched\n")
        venv = implementation / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = fake\n")
        (venv / "lib").symlink_to(external, target_is_directory=True)
        result = self.invoke(implementation, "setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe Python virtual environment contents", result.stderr)
        self.assertEqual(sentinel.read_text(), "untouched\n")

    def test_python_never_installs_through_external_interpreter_symlink(self):
        implementation = self.implementation("python", "python-linked-interpreter")
        (implementation / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12,<3.14"\n'
        )
        (implementation / "main.py").write_text("")
        self.fake_python("external-python", (3, 13, 4))
        self.fake_python("python3.13", (3, 13, 4))
        external = self.bin / "external-python"
        external_before = external.read_bytes()
        venv = implementation / ".venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = fake\n")
        (venv / "bin/python").symlink_to(external)
        result = self.invoke(implementation, "setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe Python virtual environment interpreter", result.stderr)
        self.assertTrue((venv / "bin/python").is_symlink())
        self.assertEqual(external.read_bytes(), external_before)

    def test_python_recreates_empty_and_partial_local_virtual_environments(self):
        for name in ("empty", "partial"):
            with self.subTest(name=name):
                implementation = self.implementation("python", f"python-{name}-venv")
                (implementation / "pyproject.toml").write_text(
                    '[project]\nrequires-python = ">=3.12,<3.14"\n'
                )
                (implementation / "main.py").write_text("")
                self.fake_python("python3.13", (3, 13, 4))
                venv = implementation / ".venv"
                venv.mkdir()
                if name == "partial":
                    (venv / "bin").mkdir()
                    (venv / "interrupted-install").write_text("partial\n")
                result = self.invoke(implementation, "setup")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((venv / "pyvenv.cfg").is_file())
                self.assertTrue(os.access(venv / "bin/python", os.X_OK))
                self.assertFalse((venv / "interrupted-install").exists())
                shutil.rmtree(implementation.parent)
                (self.bin / "python3.13").unlink()

    def test_python_reuses_prepared_venv_without_system_candidate(self):
        implementation = self.implementation("python", "python-reuse")
        (implementation / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12,<3.14"\n'
        )
        (implementation / "main.py").write_text("")
        self.fake_python("python3.13", (3, 13, 2))
        self.assertEqual(self.invoke(implementation, "setup").returncode, 0)
        (self.bin / "python3.13").unlink()
        result = self.invoke(implementation, "setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already prepared", result.stdout)

    def test_python_forwards_exact_arguments_exit_and_signal(self):
        implementation = self.implementation("python", "python-forward")
        (implementation / "pyproject.toml").write_text(
            '[project]\nrequires-python = ">=3.12,<3.14"\n'
        )
        (implementation / "main.py").write_text("")
        self.fake_python("python3.13", (3, 13, 2))
        arguments = ["worker", "--value", "two words", "", "'\"$;&"]
        env = {**self.env, "FAKE_APP_EXIT": "23"}
        process, _, stderr = self.invoke_with_pid(implementation, *arguments, env=env)
        self.assertEqual(process.returncode, 23, stderr)
        trace = json.loads(self.trace.read_text())
        self.assertEqual(trace["argv"], arguments)
        self.assertEqual(trace["pid"], process.pid)
        self.assertEqual(trace["launcher_env"], [])
        self.assertFalse((implementation / ".run-state/setup.lock").exists())
        env = {**self.env, "FAKE_SIGNAL": str(signal.SIGTERM)}
        result = self.invoke(implementation, "start", env=env)
        self.assertEqual(result.returncode, -signal.SIGTERM)

    def test_go_setup_is_reused_stale_and_recovers_lock(self):
        implementation = self.implementation("go")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        self.assertEqual(self.invoke(implementation, "setup").returncode, 0)
        self.assertEqual(self.invoke(implementation, "setup").returncode, 0)
        self.assertEqual(self.trace.read_text(), "1")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.24\n")
        lock = implementation / ".run-state/setup.lock"
        lock.mkdir()
        (lock / "pid").write_text("99999999\n")
        self.assertEqual(self.invoke(implementation, "setup").returncode, 0)
        self.assertEqual(self.trace.read_text(), "2")

    def test_go_persistent_module_cache_is_reused_by_execution(self):
        implementation = self.implementation("go", "go-cache")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        downloads = self.root / "go-downloads"
        environment = {**self.env, "DOWNLOAD_TRACE": str(downloads)}
        self.assertEqual(
            self.invoke(implementation, "setup", env=environment).returncode, 0)
        self.assertEqual(downloads.read_text(), "1")
        result = self.invoke(implementation, "worker", env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(downloads.read_text(), "1")
        trace = json.loads(self.trace.read_text())
        cache_root = (implementation / ".run-state/go").resolve()
        self.assertTrue(Path(trace["gopath"]).resolve().is_relative_to(cache_root))
        self.assertTrue(
            Path(trace["gomodcache"]).resolve().is_relative_to(cache_root))
        self.assertTrue(Path(trace["gocache"]).resolve().is_relative_to(cache_root))
        self.assertEqual(trace["goflags"], "-modcacherw")

        shutil.rmtree(cache_root)
        self.assertEqual(
            self.invoke(implementation, "setup", env=environment).returncode, 0)
        self.assertEqual(downloads.read_text(), "2")
        for missing, expected_count in (
            (cache_root / "gopath", "3"),
            (cache_root / "gopath/pkg", "4"),
            (cache_root / "gopath/pkg/mod", "5"),
            (cache_root / "build", "6"),
        ):
            shutil.rmtree(missing)
            repaired = self.invoke(implementation, "setup", env=environment)
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertEqual(downloads.read_text(), expected_count)

    def test_go_cache_evidence_is_bounded_and_only_follows_success(self):
        implementation = self.implementation("go", "go-cache-evidence")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        failed = self.invoke(
            implementation, "setup",
            env={**self.env, "FAKE_SETUP_EXIT": "9"},
        )
        evidence = implementation / ".run-state/go/prepared.v1"
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(evidence.exists())
        self.assertEqual(self.invoke(implementation, "setup").returncode, 0)
        self.assertLessEqual(evidence.stat().st_size, 128)
        evidence.write_text("x" * 129)
        self.assertEqual(self.invoke(implementation, "setup").returncode, 0)
        self.assertEqual(evidence.read_text(), "version=1\ncache=go\n")

    def test_go_rejects_nested_cache_symlink_before_mutating_target(self):
        implementation = self.implementation("go", "go-linked-cache")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        cache = implementation / ".run-state/go"
        cache.mkdir(parents=True)
        external = self.root / "external-go-cache"
        external.mkdir()
        (cache / "gopath").symlink_to(external, target_is_directory=True)
        result = self.invoke(implementation, "setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe Go cache path", result.stderr)
        self.assertEqual(list(external.iterdir()), [])

        root_linked = self.implementation("go", "go-linked-cache-root")
        (root_linked / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        linked_state = root_linked / ".run-state"
        linked_state.mkdir()
        sentinel = external / "prepared.v1"
        sentinel.write_text("do not remove\n")
        (linked_state / "go").symlink_to(external, target_is_directory=True)
        result = self.invoke(root_linked, "setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe Go cache path", result.stderr)
        self.assertEqual(sentinel.read_text(), "do not remove\n")

    def test_concurrent_and_interrupted_setup_never_publish_ready(self):
        implementation = self.implementation("go", "go-concurrent")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        env = {**self.env, "FAKE_SETUP_SLEEP": "2"}
        first = subprocess.Popen(
            [str(implementation / "run.sh"), "setup"],
            cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
        lock = implementation / ".run-state/setup.lock"
        deadline = time.time() + 2
        while not lock.exists() and time.time() < deadline:
            time.sleep(.01)
        self.assertTrue(lock.exists())
        concurrent = self.invoke(implementation, "setup", env=env)
        self.assertNotEqual(concurrent.returncode, 0)
        self.assertIn("already running", concurrent.stderr)
        os.killpg(first.pid, signal.SIGTERM)
        first.communicate(timeout=3)
        self.assertFalse(lock.exists())
        self.assertFalse((implementation / ".run-state/ready.v1").exists())
        repaired = self.invoke(
            implementation, "setup", env={**self.env, "FAKE_SETUP_SLEEP": "0"}
        )
        self.assertEqual(repaired.returncode, 0, repaired.stderr)

    def test_hard_interruption_before_lock_publication_recovers(self):
        implementation = self.implementation("go", "go-pre-publication")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        flag = self.root / "ln-entered"
        real_ln = shutil.which("ln", path="/usr/bin:/bin")
        self.assertIsNotNone(real_ln)
        self.executable(
            self.bin / "ln",
            "#!/bin/sh\n"
            f"if [ ! -e {shlex.quote(str(flag))} ]; then\n"
            f"  : >{shlex.quote(str(flag))}\n"
            "  while :; do sleep 1; done\n"
            "fi\n"
            f"exec {shlex.quote(real_ln)} \"$@\"\n",
        )
        process = subprocess.Popen(
            [str(implementation / "run.sh"), "setup"],
            cwd=self.root, env=self.env, start_new_session=True,
        )
        deadline = time.time() + 2
        while not flag.exists() and time.time() < deadline:
            time.sleep(.01)
        self.assertTrue(flag.exists())
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
        self.assertFalse((implementation / ".run-state/setup.lock").exists())
        recovered = self.invoke(implementation, "setup")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(
            list((implementation / ".run-state").glob("setup.owner.*")), [])

    def test_empty_lock_and_hard_interruption_during_recovery_recover(self):
        implementation = self.implementation("go", "go-recovery-interruption")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        state = implementation / ".run-state"
        (state / "setup.lock").mkdir(parents=True)
        (state / "setup.recovery").mkdir()
        flag = self.root / "recovery-entered"
        real_rm = shutil.which("rm", path="/usr/bin:/bin")
        self.assertIsNotNone(real_rm)
        self.executable(
            self.bin / "rm",
            "#!/bin/sh\n"
            f"case \"$*\" in *setup.recovery*) if [ ! -e {shlex.quote(str(flag))} ]; then\n"
            f"  : >{shlex.quote(str(flag))}; while :; do sleep 1; done; fi;; esac\n"
            f"exec {shlex.quote(real_rm)} \"$@\"\n",
        )
        process = subprocess.Popen(
            [str(implementation / "run.sh"), "setup"],
            cwd=self.root, env=self.env, start_new_session=True,
        )
        deadline = time.time() + 2
        while not flag.exists() and time.time() < deadline:
            time.sleep(.01)
        self.assertTrue(flag.exists())
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=3)
        self.assertTrue(list(state.glob("setup.recovery.*")))
        recovered = self.invoke(implementation, "setup")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertFalse((state / "setup.lock").exists())
        self.assertFalse((state / "setup.recovery").exists())
        self.assertEqual(list(state.glob("setup.recovery.*")), [])

    def test_direct_launcher_replacement_cannot_publish_readiness(self):
        implementation = self.implementation("go", "go-replaced")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        process = subprocess.Popen(
            [str(implementation / "run.sh"), "setup"],
            cwd=self.root,
            env={**self.env, "FAKE_SETUP_SLEEP": ".2"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        lock = implementation / ".run-state/setup.lock"
        deadline = time.time() + 2
        while not lock.exists() and time.time() < deadline:
            time.sleep(.01)
        replacement = implementation / "replacement"
        replacement.write_text(
            (implementation / "run.sh").read_text() + "\n# replacement\n")
        replacement.chmod(0o755)
        replacement.replace(implementation / "run.sh")
        _, stderr = process.communicate(timeout=3)
        self.assertNotEqual(process.returncode, 0, stderr)
        self.assertFalse((implementation / ".run-state/ready.v1").exists())

    def test_direct_launcher_replacement_before_fingerprinting_executes_replacement(self):
        implementation = self.implementation("go", "go-replaced-before-bind")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        entered = self.root / "mkdir-entered"
        release = self.root / "mkdir-release"
        real_mkdir = shutil.which("mkdir", path="/usr/bin:/bin")
        self.assertIsNotNone(real_mkdir)
        self.executable(
            self.bin / "mkdir",
            "#!/bin/sh\n"
            f"if [ ! -e {shlex.quote(str(entered))} ]; then\n"
            f"  : >{shlex.quote(str(entered))}\n"
            f"  while [ ! -e {shlex.quote(str(release))} ]; do sleep .01; done\n"
            "fi\n"
            f"exec {shlex.quote(real_mkdir)} \"$@\"\n",
        )
        process = subprocess.Popen(
            [str(implementation / "run.sh"), "setup"],
            cwd=self.root, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        deadline = time.time() + 2
        while not entered.exists() and time.time() < deadline:
            time.sleep(.01)
        self.assertTrue(entered.exists())
        replacement = implementation / "replacement-before-bind"
        replacement.write_text("#!/bin/sh\nexit 23\n")
        replacement.chmod(0o755)
        replacement.replace(implementation / "run.sh")
        release.touch()
        _, stderr = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 23, stderr)
        self.assertFalse((implementation / ".run-state/ready.v1").exists())

    def test_go_forwards_exact_arguments_exit_and_signal(self):
        implementation = self.implementation("go", "go-forward")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        arguments = ["worker", "-value", "two words", "", "'\"$;&"]
        process, _, stderr = self.invoke_with_pid(
            implementation, *arguments, env={**self.env, "FAKE_APP_EXIT": "29"}
        )
        self.assertEqual(process.returncode, 29, stderr)
        trace = json.loads(self.trace.read_text())
        self.assertEqual(trace["argv"], arguments)
        self.assertEqual(trace["pid"], process.pid)
        self.assertEqual(trace["launcher_env"], [])
        self.assertFalse((implementation / ".run-state/setup.lock").exists())
        result = self.invoke(
            implementation, "start",
            env={**self.env, "FAKE_SIGNAL": str(signal.SIGTERM)},
        )
        self.assertEqual(result.returncode, -signal.SIGTERM)

    def test_go_rejects_toolchain_older_than_go_mod(self):
        implementation = self.implementation("go", "go-version")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        result = self.invoke(
            implementation, "setup",
            env={**self.env, "FAKE_GO_VERSION": "go1.22.9"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not satisfy", result.stderr)

    def test_dependency_change_during_setup_never_publishes_ready(self):
        implementation = self.implementation("go", "go-changing")
        manifest = implementation / "go.mod"
        manifest.write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        process = subprocess.Popen(
            [str(implementation / "run.sh"), "setup"],
            cwd=self.root, env={**self.env, "FAKE_SETUP_SLEEP": ".3"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        state = implementation / ".run-state"
        deadline = time.time() + 2
        markers = []
        while time.time() < deadline:
            markers = list(state.glob("ready.tmp.*"))
            if markers and "dependency=go.mod:" in markers[0].read_text():
                break
            time.sleep(.01)
        self.assertTrue(markers)
        self.assertIn("dependency=go.mod:", markers[0].read_text())
        manifest.write_text("module fixture\n\ngo 1.24\n")
        _, stderr = process.communicate(timeout=3)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("changed during setup", stderr)
        self.assertFalse((implementation / ".run-state/ready.v1").exists())

    def test_java_gradle_args_preserve_boundaries_exit_and_signal(self):
        implementation = self.implementation("java")
        self.fake_java(implementation)
        arguments = [
            "start", "--text", "two words", "", "'\"$;&",
            "back\\slash", "embedded\nnewline", "trailing newline\n",
        ]
        process, _, stderr = self.invoke_with_pid(
            implementation, *arguments, env={**self.env, "FAKE_APP_EXIT": "31"}
        )
        self.assertEqual(
            process.returncode, 31,
            f"stderr={stderr!r} "
            f"trace={self.trace.read_text() if self.trace.exists() else 'missing'}",
        )
        trace = json.loads(self.trace.read_text())
        gradle = trace["argv"]
        self.assertEqual(trace["pid"], process.pid)
        self.assertEqual(trace["launcher_env"], [])
        self.assertFalse((implementation / ".run-state/setup.lock").exists())
        self.assertEqual(gradle[:2], ["--no-daemon", "run"])
        payload = gradle[2].removeprefix("--args=")
        import shlex
        self.assertEqual(shlex.split(payload), arguments)
        result = self.invoke(
            implementation, "worker",
            env={**self.env, "FAKE_SIGNAL": str(signal.SIGTERM)},
        )
        self.assertEqual(result.returncode, -signal.SIGTERM)

    def test_java_uses_isolated_persistent_gradle_home(self):
        implementation = self.implementation("java", "java-cache")
        self.fake_java(implementation)
        real_home = self.root / "real-home"
        real_gradle = real_home / ".gradle"
        real_gradle.mkdir(parents=True)
        credential = "repositoryPassword=real-user-secret"
        (real_gradle / "gradle.properties").write_text(credential + "\n")
        inherited_gradle = self.root / "inherited-gradle-home"
        setups = self.root / "gradle-setups"
        environment = {
            **self.env,
            "SETUP_TRACE": str(setups),
            "HOME": str(real_home),
            "GRADLE_USER_HOME": str(inherited_gradle),
            "GRADLE_OPTS": f"-Dgradle.user.home={real_gradle}",
            "JAVA_OPTS": f"-Dgradle.user.home={real_gradle}",
            "JAVA_TOOL_OPTIONS": f"-Dgradle.user.home={real_gradle}",
            "_JAVA_OPTIONS": f"-Dgradle.user.home={real_gradle}",
            "JDK_JAVA_OPTIONS": f"-Dgradle.user.home={real_gradle}",
        }
        setup = self.invoke(implementation, "setup", env=environment)
        self.assertEqual(setup.returncode, 0, setup.stderr)
        result = self.invoke(implementation, "worker", env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        trace = json.loads(self.trace.read_text())
        expected_home = (implementation / ".run-state/gradle-home").resolve()
        self.assertEqual(Path(trace["gradle_user_home"]).resolve(), expected_home)
        self.assertTrue(trace["cache_reused"])
        self.assertIsNone(trace["gradle_opts"])
        self.assertIsNone(trace["java_opts"])
        self.assertIsNone(trace["java_tool_options"])
        self.assertIsNone(trace["underscore_java_options"])
        self.assertIsNone(trace["jdk_java_options"])
        self.assertFalse(inherited_gradle.exists())

        gradle_home = implementation / ".run-state/gradle-home"
        shutil.rmtree(gradle_home)
        repaired = self.invoke(implementation, "setup", env=environment)
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(setups.read_text(), "2")
        for missing, expected_count in (
            (gradle_home / "caches", "3"),
            (gradle_home / "wrapper", "4"),
            (gradle_home / "wrapper/dists", "5"),
        ):
            shutil.rmtree(missing)
            repaired = self.invoke(implementation, "setup", env=environment)
            self.assertEqual(repaired.returncode, 0, repaired.stderr)
            self.assertEqual(setups.read_text(), expected_count)

        failed = self.implementation("java", "java-cache-failure")
        self.fake_java(failed)
        failure = self.invoke(
            failed, "setup",
            env={**environment, "FAKE_SETUP_EXIT": "7"},
        )
        self.assertNotEqual(failure.returncode, 0)
        self.assertNotIn("real-user-secret", failure.stderr)
        self.assertNotIn(credential, failure.stderr)
        self.assertFalse(
            (failed / ".run-state/gradle-home/prepared.v1").exists())

    def test_java_cache_evidence_is_bounded_and_repaired(self):
        implementation = self.implementation("java", "java-cache-evidence")
        self.fake_java(implementation)
        self.assertEqual(self.invoke(implementation, "setup").returncode, 0)
        evidence = implementation / ".run-state/gradle-home/prepared.v1"
        self.assertLessEqual(evidence.stat().st_size, 128)
        evidence.write_text("x" * 129)
        repaired = self.invoke(implementation, "setup")
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(evidence.read_text(), "version=1\ncache=gradle\n")
        external = self.root / "external-gradle-cache"
        external.mkdir()
        sentinel = external / "sentinel"
        sentinel.write_text("unchanged\n")
        caches = implementation / ".run-state/gradle-home/caches"
        shutil.rmtree(caches)
        caches.symlink_to(external, target_is_directory=True)
        rejected = self.invoke(implementation, "setup")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("Unsafe Gradle cache path", rejected.stderr)
        self.assertEqual(sentinel.read_text(), "unchanged\n")

    def test_java_rejects_incompatible_toolchain(self):
        implementation = self.implementation("java", "java-version")
        self.fake_java(implementation)
        result = self.invoke(
            implementation, "setup",
            env={**self.env, "FAKE_JAVA_VERSION": "15.0.2"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not satisfy Java 17", result.stderr)

    def test_malformed_oversized_partial_and_symlink_state_are_not_reused(self):
        implementation = self.implementation("go", "go-state")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        state = implementation / ".run-state"
        state.mkdir()
        for content in ("version=1\n", "x" * 4097):
            (state / "ready.v1").write_text(content)
            self.assertEqual(self.invoke(implementation, "setup").returncode, 0)
            shutil.rmtree(state)
            state.mkdir()
        outside = self.root / "outside"
        outside.write_text("version=1\n")
        (state / "ready.v1").symlink_to(outside)
        result = self.invoke(implementation, "setup")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((state / "ready.v1").is_symlink())
        self.assertEqual(outside.read_text(), "version=1\n")

    def test_failure_excerpt_is_bounded_redacted_and_log_is_removed(self):
        implementation = self.implementation("go", "go-failure")
        (implementation / "go.mod").write_text("module fixture\n\ngo 1.23\n")
        self.fake_go()
        state = implementation / ".run-state"
        state.mkdir(mode=0o777)
        outside = self.root / "outside-log"
        outside.write_text("do not replace\n")
        (state / "setup.log.tmp").symlink_to(outside)
        noisy = self.bin / "go"
        noisy.write_text(
            "#!/bin/sh\n"
            "if [ \"${1-}\" = env ]; then echo go1.24.0; exit 0; fi\n"
            "python3 - <<'PY'\n"
            "print('x' * 8000 + ' token=lower-secret')\n"
            "print('https://user:password@example.test/private')\n"
            "PY\n"
            "exit 7\n"
        )
        noisy.chmod(0o755)
        result = self.invoke(implementation, "setup")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("lower-secret", result.stderr)
        self.assertNotIn("password", result.stderr)
        self.assertIn("[redacted]", result.stderr)
        self.assertLessEqual(len(result.stderr.encode()), 4300)
        self.assertFalse((implementation / ".run-state/setup.log.tmp").exists())
        self.assertEqual(outside.read_text(), "do not replace\n")
        self.assertEqual(state.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
