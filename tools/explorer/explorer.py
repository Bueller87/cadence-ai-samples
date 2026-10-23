"""Discover a checkout and produce local command previews. No recipe execution."""
from __future__ import annotations

import ast
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote, urlsplit, urlunsplit

import yaml

from source import (InvalidSelection, Unsupported, go_classifier, go_cli,
                    go_function, go_validate, java_classifier, java_cli,
                    python_cli, python_guard, value)
from signal_metadata import (SignalMetadataError, load_metadata,
                             metadata_revision, serialize_arguments,
                             validate_arguments)


SKIP = {
    'tests', 'testdata', 'perf', 'evidence',
    '.venv', 'venv', '__pycache__', 'node_modules',
}
CADENCE_WEB_URL = 'http://localhost:8088'
CADENCE_CLUSTER = 'cluster0'
SCENARIOS = {
    'auto-research': 'Build, review, revise, and publish a durable briefing from simulated source material.',
    'ticket-routing': 'Classify StreamWave support tickets and route them through durable assignment Workflows.',
    'recurring-ai-watch': 'Classify release notes, then use an agent and LLM to produce a recurring impact report.',
}


def source_link(path):
    return '/source?path=' + quote(str(path), safe='')


def catalog(root, kind):
    path = root / (kind + '.yaml')
    result = yaml.safe_load(path.read_text())
    if not isinstance(result, dict) or not isinstance(result.get(kind), list):
        raise ValueError(f'{path.name}: expected a {kind} list.')
    seen = set()
    fields = ('id', 'provider', 'model', 'endpoint')
    for entry in result[kind]:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(f), str) or not entry[f].strip() for f in fields):
            raise ValueError(f'{path.name}: each entry needs nonempty {", ".join(fields)} strings.')
        if entry['id'] in seen:
            raise ValueError(f'{path.name}: duplicate ID {entry["id"]}.')
        seen.add(entry['id'])
    return result[kind]


def evidence(directory):
    path = directory / 'MATRIX_RESULTS.md'
    if not path.is_file() or path.is_symlink():
        return []
    records = []
    for section in re.split(r'^## ', path.read_text(), flags=re.M)[1:]:
        selections = re.search(r'Catalog selections:\s*`([^`]+)`,\s*`([^`]+)`,\s*`([^`]+)`', section)
        workflow = re.search(r'Workflow ID:\s*`([^`]+)`', section)
        run = re.search(r'Run ID:\s*`([^`]+)`', section)
        recorded_model = re.search(r'^- Model:\s*(.+)', section, re.M)
        if selections and workflow and run:
            records.append(dict(framework=selections[1], model_id=selections[2], classifier_id=selections[3],
                workflow_id=workflow[1], run_id=run[1], model_description=recorded_model[1] if recorded_model else '',
                heading=section.splitlines()[0], source=str(path)))
    return records


class Explorer:
    def __init__(self, root, cadence_web_url=CADENCE_WEB_URL,
                 cadence_cluster=CADENCE_CLUSTER, opener=urlopen,
                 workflow_timeout=1.5, prepare_timeout=300):
        self.root = Path(root).resolve()
        self.cadence_web_url = cadence_web_url.rstrip('/')
        self.cadence_cluster = cadence_cluster
        self.opener = opener
        self.workflow_timeout = workflow_timeout
        self.prepare_timeout = prepare_timeout
        self._preparation_lock = threading.Lock()
        self._preparations = {}
        self._closed = False

    def inside(self, path):
        path = Path(path).absolute()
        try:
            if not path.resolve().is_relative_to(self.root):
                return False
        except OSError:
            return False
        current = path
        while current != self.root:
            if current.is_symlink():
                return False
            parent = current.parent
            if parent == current:
                return False
            current = parent
        return not self.root.is_symlink()

    def open_launcher(self, launcher):
        launcher = Path(launcher)
        implementation = launcher.parent
        if not self.inside(implementation) or not self.inside(launcher):
            raise Unsupported(
                'This implementation needs an executable, regular run.sh inside the checkout.')
        descriptor = None
        try:
            descriptor = os.open(
                launcher, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            metadata = os.fstat(descriptor)
            if (not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_mode & 0o111 == 0):
                raise OSError('launcher is not an executable regular file')
            content = bytearray()
            while len(content) <= 1024 * 1024:
                chunk = os.read(descriptor, 65536)
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) > 1024 * 1024:
                raise OSError('launcher is too large')
            os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError as error:
            if descriptor is not None:
                os.close(descriptor)
                descriptor = None
            raise Unsupported(
                'This implementation needs an executable, regular run.sh inside the checkout.') from error
        return descriptor, hashlib.sha256(content).hexdigest()

    def launcher(self, implementation):
        launcher = Path(implementation) / 'run.sh'
        descriptor, revision = self.open_launcher(launcher)
        os.close(descriptor)
        return launcher, revision

    @staticmethod
    def dependency_paths(implementation):
        if implementation.joinpath('pyproject.toml').is_file():
            names = ['pyproject.toml']
        elif implementation.joinpath('go.mod').is_file():
            names = ['go.mod'] + (
                ['go.sum'] if implementation.joinpath('go.sum').is_file() else [])
        else:
            names = [
                name for name in (
                    'build.gradle.kts', 'settings.gradle.kts',
                    'gradle/wrapper/gradle-wrapper.properties',
                    'gradle/wrapper/gradle-wrapper.jar',
                ) if implementation.joinpath(name).is_file()
            ]
        return names

    @staticmethod
    def cksum(path):
        result = subprocess.run(
            ['cksum', str(path)], capture_output=True, text=True, timeout=2,
            check=True)
        fields = result.stdout.split()
        if len(fields) < 2 or not all(field.isdigit() for field in fields[:2]):
            raise OSError('cksum returned an unreadable fingerprint')
        return ':'.join(fields[:2])

    def preparation_state(self, implementation):
        implementation = Path(implementation)
        state = implementation / '.run-state'
        marker = state / 'ready.v1'
        if not state.exists():
            return dict(state='not prepared', detail='Prepare dependencies before starting.')
        if (not self.inside(state) or state.is_symlink() or not state.is_dir() or
                not self.inside(marker) or marker.is_symlink() or not marker.is_file()):
            return dict(state='not prepared', detail='Launcher state is incomplete or unsafe.')
        try:
            with marker.open('rb') as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError('oversized marker')
            lines = raw.decode('utf-8').splitlines()
            dependencies = self.dependency_paths(implementation)
            if any(
                    not self.inside(implementation / name) or
                    (implementation / name).is_symlink() or
                    not (implementation / name).is_file()
                    for name in dependencies):
                return dict(
                    state='not prepared',
                    detail='Dependency declaration paths are incomplete or unsafe.')
            expected = [
                'version=1',
                f'launcher={self.cksum(implementation / "run.sh")}',
                *(f'dependency={name}:{self.cksum(implementation / name)}'
                  for name in dependencies),
            ]
            valid_shape = (
                bool(dependencies) and len(lines) == len(expected) and
                lines[0] == 'version=1' and
                re.fullmatch(r'launcher=\d+:\d+', lines[1]) is not None and
                all(re.fullmatch(
                    rf'dependency={re.escape(name)}:\d+:\d+', line) is not None
                    for name, line in zip(dependencies, lines[2:]))
            )
            if not valid_shape:
                return dict(state='not prepared', detail='Launcher state is malformed or incomplete.')
            if lines != expected:
                return dict(state='stale', detail='Dependency declarations changed. Prepare again.')
        except (OSError, UnicodeError, ValueError, subprocess.SubprocessError):
            return dict(state='not prepared', detail='Launcher state is malformed or incomplete.')
        cache = None
        if (implementation / 'go.mod').is_file():
            root = state / 'go'
            cache = (
                root / 'prepared.v1',
                b'version=1\ncache=go\n',
                (root, root / 'gopath', root / 'gopath/pkg',
                 root / 'gopath/pkg/mod', root / 'build'))
        elif (implementation / 'gradlew').is_file():
            root = state / 'gradle-home'
            cache = (
                root / 'prepared.v1',
                b'version=1\ncache=gradle\n',
                (root, root / 'caches', root / 'wrapper',
                 root / 'wrapper/dists'))
        if cache is not None:
            evidence, expected_evidence, directories = cache
            if any(
                    not self.inside(path) or path.is_symlink()
                    for path in (*directories, evidence)):
                return dict(
                    state='not prepared',
                    detail='Launcher cache state is unsafe.')
            if any(not path.is_dir() for path in directories):
                return dict(
                    state='stale',
                    detail='Launcher cache is incomplete. Prepare again.')
            if not evidence.is_file():
                return dict(
                    state='stale',
                    detail='Launcher cache readiness is missing. Prepare again.')
            try:
                with evidence.open('rb') as stream:
                    cache_raw = stream.read(129)
                if len(cache_raw) > 128 or cache_raw != expected_evidence:
                    raise ValueError('invalid cache evidence')
            except (OSError, ValueError):
                return dict(
                    state='not prepared',
                    detail='Launcher cache state is malformed or incomplete.')
        return dict(state='prepared', detail='Declared dependencies are prepared.')

    @staticmethod
    def sanitize_preparation_output(raw):
        text = raw.decode('utf-8', errors='replace')
        text = re.sub(r'https?://\S+', '[redacted-url]', text)
        text = re.sub(
            r'^([ \t]*(?:\++\s*)?(?:export[ \t]+)?)'
            r'([A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|AUTHORIZATION)'
            r'[A-Za-z0-9_]*\s*=\s*)[^\r\n]*',
            r'\1\2[redacted]', text, flags=re.I | re.M)
        text = re.sub(
            r'^(authorization\s*:\s*)[^\r\n]+',
            r'\1[redacted]', text, flags=re.I | re.M)
        text = re.sub(
            r'\bBearer\s+[A-Za-z0-9._~+/=-]+',
            'Bearer [redacted]', text, flags=re.I)
        bounded = text.encode('utf-8')[-4096:]
        return bounded.decode('utf-8', errors='ignore').strip()

    @staticmethod
    def stop_process_group(process):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if process.poll() is None:
            process.wait()

    def cleanup_preparation_state(self, implementation):
        state = Path(implementation) / '.run-state'
        if (not state.exists() or state.is_symlink() or not state.is_dir() or
                not self.inside(state)):
            return
        for path in [state / 'setup.log.tmp', *state.glob('ready.tmp.*')]:
            if self.inside(path) and path.exists() and not path.is_symlink() and path.is_file():
                path.unlink()
        lock = state / 'setup.lock'
        if self.inside(lock) and lock.exists() and not lock.is_symlink():
            owner = lock / 'pid' if lock.is_dir() else lock
            try:
                pid = int(owner.read_text().strip())
                alive = pid > 0 and self.process_alive(pid)
            except (OSError, ValueError):
                alive = False
            if not alive:
                if lock.is_dir():
                    shutil.rmtree(lock)
                elif lock.is_file():
                    lock.unlink()
        recovery = state / 'setup.recovery'
        if (self.inside(recovery) and recovery.exists() and
                not recovery.is_symlink() and recovery.is_dir()):
            shutil.rmtree(recovery)
        for recovery in state.glob('setup.recovery.*'):
            if (not self.inside(recovery) or recovery.is_symlink() or
                    not recovery.is_dir()):
                continue
            try:
                pid = int(recovery.name.rsplit('.', 1)[1])
            except ValueError:
                continue
            if not self.process_alive(pid):
                shutil.rmtree(recovery)

    def remove_readiness_marker(self, implementation):
        marker = Path(implementation) / '.run-state' / 'ready.v1'
        if (self.inside(marker) and marker.exists() and not marker.is_symlink() and
                marker.is_file()):
            marker.unlink()

    @staticmethod
    def process_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def descriptor_cksum(descriptor):
        os.lseek(descriptor, 0, os.SEEK_SET)
        result = subprocess.run(
            ['cksum', f'/dev/fd/{descriptor}'], pass_fds=(descriptor,),
            capture_output=True, text=True, timeout=2, check=True)
        fields = result.stdout.split()
        if len(fields) < 2 or not all(field.isdigit() for field in fields[:2]):
            raise OSError('cksum returned an unreadable fingerprint')
        os.lseek(descriptor, 0, os.SEEK_SET)
        return ':'.join(fields[:2])

    @staticmethod
    def forwarded_url(name, value):
        if value != value.strip() or any(character.isspace() for character in value):
            raise InvalidSelection(f'{name} contains an invalid package or proxy URL.')
        parsed = urlsplit(value)
        try:
            parsed.port
            host_is_valid = (
                re.fullmatch(r'[A-Za-z0-9.-]+', parsed.hostname or '') is not None or
                ipaddress.ip_address(parsed.hostname or '') is not None)
        except ValueError:
            host_is_valid = False
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or
                not host_is_valid or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment):
            raise InvalidSelection(
                f'{name} must be an HTTP(S) URL without credentials, query, or fragment.')
        return value

    def preparation_environment(self, launcher, launcher_cksum, isolated_home):
        environment = {
            'PATH': os.environ.get('PATH', os.defpath),
            'HOME': str(isolated_home),
            'CADENCE_RECIPE_LAUNCHER_PATH': str(launcher),
            'CADENCE_RECIPE_LAUNCHER_CKSUM': launcher_cksum,
            'CADENCE_RECIPE_LAUNCHER_BOUND': '1',
        }
        for name in ('LANG', 'LC_ALL', 'JAVA_HOME', 'TMPDIR', 'TMP', 'TEMP',
                     'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE',
                     'CURL_CA_BUNDLE', 'NO_PROXY', 'no_proxy'):
            if name in os.environ:
                environment[name] = os.environ[name]
        for name in ('PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'HTTP_PROXY',
                     'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy',
                     'all_proxy'):
            if value := os.environ.get(name):
                environment[name] = self.forwarded_url(name, value)
        return environment

    def close(self):
        with self._preparation_lock:
            self._closed = True
            preparations = list(self._preparations.items())
        for process, _ in preparations:
            self.stop_process_group(process)
        for _, implementation in preparations:
            self.cleanup_preparation_state(implementation)

    def run_preparation(self, launcher, expected_revision=None):
        descriptor, revision = self.open_launcher(launcher)
        if expected_revision is not None and revision != expected_revision:
            os.close(descriptor)
            raise InvalidSelection(
                'The launcher changed immediately before preparation. Retry from the refreshed selection.')
        launcher_cksum = self.descriptor_cksum(descriptor)
        isolated_home = tempfile.TemporaryDirectory(
            prefix='cadence-explorer-prepare-')
        try:
            environment = self.preparation_environment(
                launcher, launcher_cksum, isolated_home.name)
            with self._preparation_lock:
                if self._closed:
                    raise Unsupported('Explorer is shutting down.')
                process = subprocess.Popen(
                    ['/bin/sh', f'/dev/fd/{descriptor}', 'setup'],
                    cwd=launcher.parent, env=environment, pass_fds=(descriptor,),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, start_new_session=True)
                self._preparations[process] = launcher.parent
        except BaseException:
            isolated_home.cleanup()
            raise
        finally:
            os.close(descriptor)
        output = bytearray()
        discard_partial_line = False

        def drain():
            nonlocal discard_partial_line
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    return
                if discard_partial_line:
                    newline = chunk.find(b'\n')
                    if newline < 0:
                        continue
                    chunk = chunk[newline + 1:]
                    discard_partial_line = False
                output.extend(chunk)
                if len(output) > 1024 * 1024:
                    del output[:len(output) - 1024 * 1024]
                    newline = output.find(b'\n')
                    if newline < 0:
                        output.clear()
                        discard_partial_line = True
                    else:
                        del output[:newline + 1]

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        timed_out = False
        try:
            process.wait(timeout=self.prepare_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self.stop_process_group(process)
            self.cleanup_preparation_state(launcher.parent)
        except BaseException:
            self.stop_process_group(process)
            self.cleanup_preparation_state(launcher.parent)
            raise
        finally:
            reader.join(timeout=2)
            if process.stdout:
                process.stdout.close()
            with self._preparation_lock:
                self._preparations.pop(process, None)
            isolated_home.cleanup()
        return process.returncode, timed_out, self.sanitize_preparation_output(bytes(output))

    def read_cli(self, path, kind):
        entry = path / {
            'python': 'main.py',
            'go': 'main.go',
            'java': 'src/main/java/com/example/cadence/recipe/RecipeApplication.java',
        }.get(kind, 'main')
        if not entry.is_file() or not self.inside(entry):
            raise Unsupported('Implementation source must stay inside the checkout and must not be a symlink.')
        pattern = {
            'python': '*.py',
            'go': '*.go',
            'java': 'src/main/java/**/*.java',
        }.get(kind)
        if pattern is None:
            raise Unsupported('No readable CLI adapter for this implementation yet. See the recipe README.')
        sources = list(path.glob(pattern))
        if any(not self.inside(source) for source in sources):
            raise Unsupported('Implementation source must stay inside the checkout and must not be a symlink.')
        if kind == 'python':
            cli = python_cli(path/'main.py')
        elif kind == 'go':
            cli = go_cli(path)
        else:
            cli = java_cli(path)
        return cli, sources

    def implementations(self, recipe):
        variants = []
        for language in sorted(p for p in recipe.iterdir() if p.is_dir() and not p.name.startswith('.') and p.name not in SKIP):
            # Unknown languages remain visible and may gain a reader later.
            candidates = [language]
            candidates.extend(p for p in sorted(language.iterdir()) if p.is_dir() and p.name not in SKIP and not p.name.startswith('.'))
            for path in candidates:
                entry = path / (
                    'main.py' if language.name == 'python' else
                    'main.go' if language.name == 'go' else
                    'src/main/java/com/example/cadence/recipe/RecipeApplication.java'
                    if language.name == 'java' else 'main'
                )
                if path == language and not entry.exists() and len(candidates) > 1:
                    continue
                if not self.inside(path):
                    continue
                framework = path.name if path != language and path.name != 'bare' else None
                variant = dict(id=str(path.relative_to(self.root)), language=language.name, framework=framework,
                    source=source_link(entry.relative_to(self.root)), supported=False, reason=None)
                try:
                    if language.name not in {'python', 'go', 'java'} or not entry.is_file():
                        raise Unsupported('No readable CLI adapter for this implementation yet. See the recipe README.')
                    cli, _ = self.read_cli(path, language.name)
                    accepted_flags = set(cli['root'])
                    if cli['kind'] == 'python':
                        accepted_flags.update(cli['commands']['worker'])
                    variant.update(supported=True, kind=cli['kind'], modes=cli['modes'],
                        actions=cli['actions'], generic=cli['generic'],
                        model='--model-id' in accepted_flags,
                        classifier=('--classifier-id' if cli['kind'] in {'python','java'}
                                    else '-classifier-id') in accepted_flags,
                        defaults={key:spec.get('default') for key,spec in cli['root'].items() if spec.get('default') is not None})
                    if cli['kind'] == 'go' and not cli['generic']:
                        variant.update(live_modes=cli['live_modes'], mock_modes=cli['mock_modes'],
                            batch_limits={key:cli.get('env',{}).get(key) for key in ('maximumLiveBatchCount','maximumLiveConcurrency')})
                except (OSError, SyntaxError, ValueError, TypeError, AttributeError) as error:
                    variant['reason'] = str(error)
                variants.append(variant)
        return variants

    def snapshot(self):
        state = dict(recipes=[], models=[], classifiers=[], catalog_errors={},
            read_at=datetime.now(timezone.utc).isoformat(timespec='seconds'))
        for kind in ('models', 'classifiers'):
            try:
                if not self.inside(self.root/(kind+'.yaml')):
                    raise ValueError('Catalog files must stay inside the checkout.')
                state[kind] = catalog(self.root, kind)
            except (OSError, ValueError, yaml.YAMLError) as error:
                state['catalog_errors'][kind] = str(error)
        recipes = self.root / 'recipes'
        if recipes.is_dir():
            for recipe in sorted(p for p in recipes.iterdir() if p.is_dir() and not p.name.startswith('.')):
                if not self.inside(recipe):
                    continue
                readme = recipe / 'README.md'
                title = recipe.name
                if readme.is_file() and self.inside(readme):
                    heading = re.search(r'^# (.+)', readme.read_text(), re.M)
                    if heading:
                        title = heading[1]
                state['recipes'].append(dict(id=recipe.name, title=title,
                    description=SCENARIOS.get(recipe.name, 'Explore this recipe from its local README and implementation.'),
                    readme=source_link(readme.relative_to(self.root)) if readme.is_file() and self.inside(readme) else None,
                    variants=self.implementations(recipe)))
        return state

    def selected_catalog(self, state, kind, selected):
        if kind in state['catalog_errors']:
            raise InvalidSelection(state['catalog_errors'][kind])
        entry = next((e for e in state[kind] if e['id'] == selected), None)
        if entry is None:
            raise InvalidSelection(f'Unknown {kind} ID. Refresh the explorer to read the current catalog.')
        return entry

    def signal_metadata(self, recipe_id):
        recipe = self.root / 'recipes' / recipe_id
        if not recipe.is_dir() or not self.inside(recipe):
            raise SignalMetadataError(
                'Signal metadata recipe must stay inside the checkout.')
        return load_metadata(recipe, self.inside)

    def signal_metadata_state(self, recipe_id):
        try:
            signals = self.signal_metadata(recipe_id)
            return signals, metadata_revision(signals), None
        except SignalMetadataError as error:
            return [], None, str(error)

    def validated_signal_request(self, request):
        changed = 'Signal metadata changed; refresh the Explorer and retry.'
        rendered_revision = request.get('signal_metadata_revision')
        if not isinstance(rendered_revision, str):
            raise InvalidSelection(changed)
        try:
            signals = self.signal_metadata(request.get('sample'))
        except (SignalMetadataError, TypeError) as error:
            raise InvalidSelection(changed) from error
        if rendered_revision != metadata_revision(signals):
            raise InvalidSelection(changed)
        signal_id = request.get('signal_id')
        signal = next(
            (candidate for candidate in signals
             if candidate['id'] == signal_id), None)
        if signal is None:
            raise InvalidSelection(
                'Choose a currently declared Workflow Signal control.')
        try:
            values = validate_arguments(signal, request.get('arguments', {}))
            return signal, serialize_arguments(values)
        except SignalMetadataError as error:
            raise InvalidSelection(str(error)) from error

    def resolve(self, request):
        state = self.snapshot()
        recipe = next((r for r in state['recipes'] if r['id'] == request.get('sample')), None)
        if recipe is None:
            raise InvalidSelection('Choose a recipe from the current checkout.')
        variant = next((v for v in recipe['variants'] if v['id'] == request.get('implementation')), None)
        if variant is None or not variant['supported']:
            raise InvalidSelection(variant['reason'] if variant else 'Choose an implementation.')
        mode = request.get('mode', '')
        if variant['modes'] and mode not in variant['modes']:
            raise InvalidSelection('Choose an execution mode explicitly.')
        path = self.root / variant['id']
        # Reread the same source at resolution time; never reuse stale capabilities.
        cli, source_files = self.read_cli(path, variant['kind'])
        live = mode == 'live' if cli['generic'] or cli['kind'] == 'python' else mode in cli['live_modes']
        model = (self.selected_catalog(state,'models',request.get('model'))
                 if variant['model'] and (live or not cli['generic']) else None)
        if not cli['generic'] and cli['kind'] == 'go' and variant['classifier'] and mode == 'worker':
            worker_mock = cli.get('env',{}).get('mockClassifierID')
            live = request.get('classifier') != worker_mock
        mock_id = cli.get('env',{}).get('mockClassifierID')
        classifier = None
        if (variant['classifier']
                and (live or (not cli['generic'] and cli['kind'] != 'go'))):
            classifier = self.selected_catalog(state,'classifiers',request.get('classifier'))
        if cli['kind'] == 'python' and (variant['model'] or variant['classifier']):
            selection = SimpleNamespace(model=SimpleNamespace(**model) if model else None,
                                        classifier=SimpleNamespace(**classifier) if classifier else None)
            if live:
                classifier_guard = path / 'classifier.py'
                if classifier and classifier_guard.is_file():
                    python_guard(classifier_guard, selection.classifier, 'validate_classifier')
                guard = path / 'live.py'
                if not guard.is_file():
                    guard = path / 'inference.py'
                if classifier and not classifier_guard.is_file():
                    python_guard(guard, selection)
                elif model:
                    python_guard(guard, selection.model)
            else:
                # Read the CLI's classifier restriction even though mock mode makes no AI calls.
                main = ast.parse((path/'main.py').read_text())
                for node in ast.walk(main):
                    if isinstance(node,ast.If) and isinstance(node.test,ast.Compare) and any(isinstance(op,ast.NotIn) for op in node.test.ops):
                        test = ast.unparse(node.test)
                        if 'classifier' in test and isinstance(node.test.comparators[0],(ast.Set,ast.Tuple,ast.List)):
                            env = {'selection':selection,'classifier':(classifier['id'],classifier['provider'])}
                            if value(node.test,env):
                                raise InvalidSelection('This classifier is a catalog candidate, not implemented by the selected CLI.')
        if cli['kind'] == 'go' and live and classifier:
            go_classifier(cli,classifier)
        if cli['kind'] == 'java' and live and classifier:
            java_classifier(cli,classifier)
        parts = [recipe['id'],variant['language'],variant.get('framework') or 'bare',mode or 'default']
        parts.extend(e['id'] for e in (model,classifier) if e)
        task_list = '-'.join(parts)
        workflow_id = task_list+'-demo'
        flags = cli['root']
        domain_flag = '--domain' if cli['kind'] in {'python','java'} else '-domain'
        domain = flags[domain_flag].get('default')
        if not isinstance(domain,str) or not domain:
            raise Unsupported('No readable default Cadence domain in this CLI.')
        if cli['kind'] == 'python' and cli['generic']:
            common = ['./run.sh',domain_flag,domain,
                      '--task-list',task_list]
            worker = list(common)
            worker_scope = cli['commands']['worker']
            for flag,config in (('--model-id',model),('--classifier-id',classifier)):
                if config and flag in flags:
                    worker += [flag,config['id']]
            worker += ['worker','--mode',mode]
            for flag,config in (('--model-id',model),('--classifier-id',classifier)):
                if config and flag in worker_scope:
                    worker += [flag,config['id']]
            if live and '--confirm-live' in worker_scope:
                worker += ['--confirm-live']
            starter = common+['--workflow-id',workflow_id,'start','--mode',mode]
            controls = []
        elif cli['kind'] == 'python':
            base = ['./run.sh',domain_flag,domain,
                    '--task-list',task_list,'--workflow-id',workflow_id]
            for flag,config in (('--model-id',model),('--classifier-id',classifier)):
                if config and flag in flags:
                    base += [flag,config['id']]
            worker = base+['worker','--mode',mode]
            starter = base+['start','--mode',mode]
            worker_scope = cli['commands']['worker']
            for flag,config in (('--model-id',model),('--classifier-id',classifier)):
                if config and flag in worker_scope:
                    worker += [flag,config['id']]
            if live and '--confirm-live' in cli['commands']['worker']:
                worker += ['--confirm-live']
            controls = []
            for command in cli['commands']:
                if command not in {'worker','start'} and not any(s.get('required') for s in cli['commands'][command].values()):
                    controls.append(['./run.sh',domain_flag,domain,'--workflow-id',workflow_id,command])
        elif cli['kind'] == 'go' and cli['generic']:
            common = [domain_flag,domain,'-task-list',task_list]
            worker_args = ['worker','-mode',mode]+common
            if live and classifier:
                worker_args += ['-classifier-id',classifier['id']]
            if live and '-confirm-live' in flags:
                worker_args += ['-confirm-live']
            starter_args = ['start','-mode',mode]+common
            if '-workflow-id' in flags:
                starter_args += ['-workflow-id',workflow_id]
            worker = ['./run.sh']+worker_args
            starter = ['./run.sh']+starter_args
            controls = []
        elif cli['kind'] == 'go':
            if mode not in cli['live_modes'] + cli['mock_modes'] + ['worker']:
                raise InvalidSelection('This action needs identifiers from a running ticket. Follow the manual acknowledgment section in the README.' if mode=='acknowledge' else
                    'No safe generated command flow is available for this action. Follow the recipe README.')
            base = ['./run.sh',domain_flag,domain,'-task-list',task_list]
            if '-workflow-id' in flags:
                base += ['-workflow-id',workflow_id]
            if variant['classifier']:
                classifier_id = classifier['id'] if classifier else mock_id
                base += ['-classifier-id',classifier_id]
            worker = base+['-mode','worker']
            starter = base+['-mode',mode] if mode != 'worker' else None
            if live and '-confirm-live' in flags:
                worker += ['-confirm-live']
                if starter and mode != 'live-batch':
                    starter += ['-confirm-live']
            controls = []
            if mode in {'batch','live-batch'}:
                # These inputs are explicit user choices, not guessed live defaults.
                if mode == 'live-batch':
                    try:
                        count, concurrency = int(request.get('count','')), int(request.get('concurrency',''))
                    except ValueError as error:
                        raise InvalidSelection('Enter an explicit live ticket count and concurrency.') from error
                    env = {**cli['env'],'trim':str.strip,'config':SimpleNamespace(
                        ClassifierID=classifier_id,CountExplicit=True,ConcurrencyExplicit=True,
                        Count=count,Concurrency=concurrency,SLA=1,TaskList=task_list,
                        TaskListExplicit=True,ConfirmLive=True,Sample=flags['-sample']['default'],
                        InputTokenPricePerMillion=None)}
                    go_validate(go_function(cli['source'],'Validate',receiver='LiveBatchConfig'),env)
                    go_validate(go_function(cli['source'],'Validate',receiver='BatchConfig'),env)
                    starter += ['-count',str(count),'-concurrency',str(concurrency),'-confirm-live']
        else:
            common = [domain_flag,domain,'--task-list',task_list]
            worker_args = ['worker','--mode',mode]+common
            if live and classifier:
                worker_args += ['--classifier-id',classifier['id']]
            if live and '--confirm-live' in flags:
                worker_args += ['--confirm-live']
            starter_args = ['start','--mode',mode]+common+[
                '--workflow-id',workflow_id]
            worker = ['./run.sh']+worker_args
            starter = ['./run.sh']+starter_args
            controls = []
        launcher, launcher_revision = self.launcher(path)
        preparation = self.preparation_state(path)
        setup = ['cd '+shlex.quote(str(path))+' && ./run.sh setup']
        record = next((e for e in evidence(self.root/'recipes'/recipe['id']) if live and
            e['framework']==variant['framework'] and model and e['model_id']==model['id'] and classifier and
            e['classifier_id']==classifier['id'] and model['model'] in e['model_description']),None)
        if record:
            record = {**record,'source':source_link(Path(record['source']).relative_to(self.root))}
        credential_pattern = r'(?:get\(|Getenv\(|environment\.read\()"([A-Z_]+AI_KEY)"'
        credentials = sorted(set(re.findall(
            credential_pattern, '\n'.join(p.read_text() for p in source_files)))) if live else []
        # The source validators identify which selections are keyless.
        if model and model['provider']=='ollama':
            credentials = [c for c in credentials if c!='MODEL_AI_KEY']
        if classifier and classifier['provider']=='laya':
            credentials = [c for c in credentials if c!='CLASSIFIER_AI_KEY']
        prefix = 'cd '+shlex.quote(str(path))+' && '
        if cli['generic'] or cli['kind'] != 'java':
            for command in [worker]+([starter] if starter else [])+controls:
                self.validate_command(cli,command)
        signals, signal_metadata_revision, signal_metadata_error = (
            self.signal_metadata_state(recipe['id']))
        return dict(setup='\n'.join(setup),worker=prefix+shlex.join(worker),
            client='\n'.join(prefix+shlex.join(c) for c in ([starter] if starter else [])+controls),
            start=prefix+shlex.join(starter) if starter else '',
            controls=[dict(name=c[-1],command=prefix+shlex.join(c)) for c in controls],
            domain=domain,task_list=task_list,
            workflow_id=workflow_id if ('--workflow-id' in flags or '-workflow-id' in flags) else None,
            cadence_web_url=self.cadence_web_url, cadence_cluster=self.cadence_cluster,
            implementation=variant['id'],mode=mode,live=live,model=model,classifier=classifier,
            launcher_revision=launcher_revision,preparation=preparation,
            evidence=record,credentials=credentials,local=self.local_commands(model,classifier) if live else [],
            signals=signals,signal_metadata_revision=signal_metadata_revision,
            signal_metadata_error=signal_metadata_error,
            mock=(mode=='mock' if cli['generic'] or cli['kind']=='python'
                  else mode in cli['mock_modes'] or (mode=='worker' and not live)),
            read_at=state['read_at'],note='Recorded live evidence is historical, not a fresh run of this checkout. Read the evidence file for scope and limitations.' if record else
                'No matching full live execution record found in this recipe’s MATRIX_RESULTS.md.' if live else
                'Mock execution makes no provider calls.')

    def check(self, request):
        resolved = self.resolve(request)
        checked_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        path = self.root / resolved['implementation']
        cli, _ = self.read_cli(path, 'python'
                               if path.joinpath('main.py').is_file() else
                               'java' if path.joinpath(
                                   'src/main/java/com/example/cadence/recipe/RecipeApplication.java').is_file()
                               else 'go')
        address_flag = ('--target' if cli['kind'] == 'python' else
                        '--address' if cli['kind'] == 'java' else '-address')
        address = cli['root'].get(address_flag, {}).get('default')
        services = [self.tcp_probe('Cadence', address)]
        services.append(self.http_probe('Cadence-Web', 'http://localhost:8088', expect_json=False))
        if resolved['mock']:
            services.append(dict(name='Inference', state='unavailable check',
                endpoint=None, detail='Mock mode does not use inference services.'))
        else:
            if resolved['model']:
                services.append(self.model_probe(resolved['model']))
            if resolved['classifier']:
                services.append(self.classifier_probe(resolved['classifier']))
        return dict(checked_at=checked_at, selection=self.selection_key(resolved), services=services)

    def workflow_run(self, request):
        resolved = self.resolve(request)
        workflow_id = resolved.get('workflow_id')
        if not isinstance(workflow_id, str) or not workflow_id:
            workflow_id = request.get('workflow_id')
            if not isinstance(workflow_id, str) or not workflow_id.strip():
                raise Unsupported(
                    'Enter one exact Workflow ID before resolving a run.')
            workflow_id = workflow_id.strip()
            if (len(workflow_id) > 255
                    or any(ord(character) < 32 for character in workflow_id)):
                raise InvalidSelection('Enter a valid Workflow ID.')
        previous_run_id = request.get('run_id')
        if previous_run_id:
            if (not isinstance(previous_run_id, str)
                    or len(previous_run_id) > 255
                    or any(ord(character) < 32
                        for character in previous_run_id)):
                raise InvalidSelection('Enter a valid previous Run ID.')
            previous_run_id = previous_run_id.strip()
        checked_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        result = dict(state='unreachable', domain=resolved['domain'],
            cluster=self.cadence_cluster, workflow_id=workflow_id,
            run_id=None, checked_at=checked_at)
        segments = [quote(value, safe='') for value in (
            resolved['domain'], self.cadence_cluster, workflow_id)]
        endpoint = (self.cadence_web_url + '/api/domains/' + segments[0] + '/'
            + segments[1] + '/workflows/' + segments[2])
        try:
            with self.opener(Request(endpoint, headers={'Accept':'application/json'}),
                    timeout=self.workflow_timeout) as response:
                body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise ValueError('response is too large')
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise ValueError('response is not an object')
            workflow_info = payload.get('workflowExecutionInfo')
            if workflow_info is None:
                result.update(state='no execution',
                    detail='No current execution was found for this Workflow ID.')
                return result
            run_id = workflow_info.get('workflowExecution', {}).get('runId')
            if not isinstance(run_id, str) or not run_id.strip():
                result.update(state='malformed response',
                    detail='Cadence-Web did not return a current Run ID.')
            elif len(run_id) > 255 or any(ord(character) < 32 for character in run_id):
                result.update(state='malformed response',
                    detail='Cadence-Web returned an invalid Run ID.')
            elif previous_run_id and previous_run_id != run_id:
                result.update(state='stale run', run_id=run_id,
                    detail=('The previous Run ID is stale. The current Run ID '
                        'changed, which can happen after Continue-As-New.'))
            else:
                result.update(state='found', run_id=run_id,
                    detail='Resolved the current Run ID through Cadence-Web.')
        except HTTPError as error:
            state = ('no execution' if error.code == 404 else
                'authentication required' if error.code in {401,403} else
                'unreachable')
            detail = ('No current execution was found for this Workflow ID.'
                if state == 'no execution' else
                'Cadence-Web requires authentication for this lookup.'
                if state == 'authentication required' else
                f'Cadence-Web responded with HTTP {error.code}.')
            error.close()
            result.update(state=state, detail=detail)
        except (URLError, TimeoutError, OSError) as error:
            result['detail'] = self.safe_error(error, 'Cadence-Web could not be reached.')
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
            result.update(state='malformed response',
                detail='Cadence-Web returned an unreadable workflow response.')
        return result

    def workflow_signal(self, request):
        signal_id = request.get('signal_id')
        if not isinstance(signal_id, str):
            raise InvalidSelection('Choose a declared Workflow Signal control.')
        displayed_run = request.get('run_id')
        if (not isinstance(displayed_run, str) or not displayed_run.strip()
                or len(displayed_run) > 255
                or any(ord(character) < 32 for character in displayed_run)):
            raise InvalidSelection('Resolve the current Run ID before sending a Signal.')
        signal, signal_input = self.validated_signal_request(request)
        current = self.workflow_run(request)
        result = dict(state=current['state'], signal_id=signal_id,
            signal_name=signal['name'], run_id=displayed_run,
            detail=current['detail'])
        if current['state'] != 'found':
            return result
        if current['run_id'] != displayed_run:
            return dict(state='stale run', signal_id=signal_id,
                signal_name=signal['name'], run_id=displayed_run,
                detail='The current Run ID changed. Resolve the latest run and retry.')
        signal, signal_input = self.validated_signal_request(request)
        result['signal_name'] = signal['name']
        segments = [quote(value, safe='') for value in (
            current['domain'], current['cluster'], current['workflow_id'], displayed_run)]
        endpoint = (self.cadence_web_url + '/api/domains/' + segments[0] + '/'
            + segments[1] + '/workflows/' + segments[2] + '/' + segments[3] + '/signal')
        payload = dict(signalName=signal['name'])
        if signal_input is not None:
            payload['signalInput'] = signal_input
        body = json.dumps(payload, separators=(',',':')).encode()
        request_object = Request(endpoint, method='POST', data=body,
            headers={'Accept':'application/json', 'Content-Type':'application/json'})
        try:
            with self.opener(request_object, timeout=self.workflow_timeout) as response:
                response_body = response.read(1024 * 1024 + 1)
            if len(response_body) > 1024 * 1024:
                raise ValueError('response is too large')
            response_payload = json.loads(response_body)
            if not isinstance(response_payload, dict):
                raise ValueError('response is not an object')
            result.update(state='accepted',
                detail='Cadence-Web accepted the Signal; Workflow processing is not yet confirmed.')
        except HTTPError as error:
            state = ('no execution' if error.code == 404 else
                'authentication required' if error.code in {401,403} else
                'rejected')
            detail = ('The resolved Workflow execution is no longer available.'
                if state == 'no execution' else
                'Cadence-Web requires authentication for this Signal.'
                if state == 'authentication required' else
                f'Cadence-Web rejected the Signal with HTTP {error.code}.')
            error.close()
            result.update(state=state, detail=detail)
        except (URLError, TimeoutError, OSError) as error:
            result.update(state='unreachable',
                detail=self.safe_error(error, 'Cadence-Web could not be reached.'))
        except (ValueError, TypeError):
            result.update(state='malformed response',
                detail='Cadence-Web returned an unreadable Signal response.')
        return result

    def selection_key(self, resolved):
        return '|'.join(str(value or '') for value in (
            resolved['implementation'], resolved['mode'],
            resolved['model']['id'] if resolved['model'] else '',
            resolved['classifier']['id'] if resolved['classifier'] else ''))

    def tcp_probe(self, name, address, timeout=1.5):
        if not isinstance(address, str) or ':' not in address:
            return dict(name=name, state='unavailable check', endpoint=address,
                detail='No readable service address is configured.')
        host, port_text = address.rsplit(':', 1)
        endpoint = f'{host}:{port_text}'
        try:
            with socket.create_connection((host, int(port_text)), timeout=timeout):
                return dict(name=name, state='reachable', endpoint=endpoint,
                    detail='TCP connection accepted. Domain and Worker readiness are not verified.')
        except (OSError, ValueError) as error:
            return dict(name=name, state='unreachable', endpoint=endpoint,
                detail=self.safe_error(error, 'Connection failed.'))

    def http_probe(self, name, endpoint, timeout=1.5, expect_json=True):
        try:
            with urlopen(Request(endpoint, headers={'Accept':'application/json'}), timeout=timeout) as response:
                body = response.read(1024 * 1024)
                if expect_json:
                    json.loads(body)
                return dict(name=name, state='reachable', endpoint=endpoint,
                    detail=f'HTTP {response.status} responded.')
        except HTTPError as error:
            state = ('reachable' if 300 <= error.code < 400 else
                'authentication required' if error.code in {401, 403} else 'unreachable')
            error.close()
            return dict(name=name, state=state, endpoint=endpoint,
                detail=f'HTTP {error.code} responded.')
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            return dict(name=name, state='unreachable', endpoint=endpoint,
                detail=self.safe_error(error, 'Request failed.'))

    def model_probe(self, model):
        if model['provider'] != 'ollama':
            return dict(name=f'Model · {model["id"]}', state='unavailable check',
                endpoint=model['endpoint'], detail='No non-billable model health check is configured.')
        endpoint = model['endpoint'].rstrip('/') + '/api/tags'
        try:
            with urlopen(Request(endpoint, headers={'Accept':'application/json'}), timeout=1.5) as response:
                payload = json.load(response)
            result = dict(name=f'Model · {model["id"]}', state='reachable', endpoint=endpoint,
                detail=f'HTTP {response.status} responded.')
            available = {entry.get('name') for entry in payload.get('models', []) if isinstance(entry, dict)}
            if model['model'] not in available:
                result.update(state='missing model',
                    detail=f'Ollama is reachable, but {model["model"]} is not installed.')
            else:
                result['detail'] = f'Ollama lists {model["model"]}. The model is not warmed yet.'
        except HTTPError as error:
            state = 'authentication required' if error.code in {401,403} else 'unreachable'
            error.close()
            result = dict(name=f'Model · {model["id"]}', state=state, endpoint=endpoint,
                detail=f'HTTP {error.code} responded.')
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError, AttributeError) as error:
            result = dict(name=f'Model · {model["id"]}', state='unreachable', endpoint=endpoint,
                detail=self.safe_error(error, 'Malformed Ollama response.'))
        return result

    def classifier_probe(self, classifier):
        if classifier['provider'] != 'laya':
            return dict(name=f'Classifier · {classifier["id"]}', state='unavailable check',
                endpoint=classifier['endpoint'],
                detail='No non-billable classifier health check is configured.')
        endpoint = urlsplit(classifier['endpoint'])
        health = urlunsplit((endpoint.scheme, endpoint.netloc, '/health', '', ''))
        return self.http_probe(f'Classifier · {classifier["id"]}', health)

    def warmup(self, request):
        resolved = self.resolve(request)
        components = []
        if resolved['mock']:
            return dict(selection=self.selection_key(resolved), components=[],
                note='Mock mode does not need local inference warm-up.')
        model, classifier = resolved['model'], resolved['classifier']
        if model and model['provider'] == 'ollama':
            endpoint = model['endpoint'].rstrip('/') + '/api/generate'
            body = dict(model=model['model'], prompt='Say ok.', stream=False, keep_alive='30m')
            components.append(self.post_json(f'Model · {model["id"]}', endpoint, body, timeout=15))
        if classifier and classifier['provider'] == 'laya':
            body = dict(state='Version: 0.0.1\nRelease notes: warmup', model=classifier['model'],
                questions=dict(relevant=dict(type='choice',
                    instructions='Is this a warmup request?',
                    criteria=dict(yes='It is a warmup.', no='It is not a warmup.'))))
            components.append(self.post_json(f'Classifier · {classifier["id"]}',
                classifier['endpoint'], body, timeout=30, required_key='answers'))
        note = ('Only selected loopback inference components were exercised.'
            if components else 'No selected local inference component needs warm-up.')
        return dict(selection=self.selection_key(resolved), components=components, note=note)

    def prepare(self, request):
        allowed = {
            'sample', 'implementation', 'mode', 'model', 'classifier',
            'count', 'concurrency', 'launcher_revision',
        }
        unexpected = sorted(set(request) - allowed)
        if unexpected:
            raise InvalidSelection(
                'Preparation accepts only the current selection and launcher revision.')
        submitted_revision = request.get('launcher_revision')
        if (not isinstance(submitted_revision, str) or
                not re.fullmatch(r'[0-9a-f]{64}', submitted_revision)):
            raise InvalidSelection('A current launcher revision is required.')
        selection = {key: value for key, value in request.items()
                     if key != 'launcher_revision'}
        resolved = self.resolve(selection)
        if resolved['launcher_revision'] != submitted_revision:
            raise InvalidSelection(
                'The launcher changed. Resolve the selection again before preparing.')
        implementation = self.root / resolved['implementation']
        launcher, immediate_revision = self.launcher(implementation)
        if immediate_revision != submitted_revision:
            raise InvalidSelection(
                'The launcher changed immediately before preparation. Retry from the refreshed selection.')
        returncode, timed_out, excerpt = self.run_preparation(
            launcher, expected_revision=submitted_revision)
        try:
            _, final_revision = self.launcher(implementation)
        except Unsupported:
            final_revision = None
        if final_revision != submitted_revision:
            self.remove_readiness_marker(implementation)
            return dict(
                state='stale', selection=self.selection_key(resolved),
                launcher_revision=submitted_revision,
                detail='The launcher changed during preparation. Resolve the selection and prepare again.',
                diagnostic=excerpt)
        state = self.preparation_state(implementation)
        if timed_out:
            return dict(
                state='failed', selection=self.selection_key(resolved),
                launcher_revision=submitted_revision,
                detail='Preparation timed out and its process group was stopped.',
                diagnostic=excerpt)
        if returncode != 0:
            return dict(
                state='failed', selection=self.selection_key(resolved),
                launcher_revision=submitted_revision,
                detail=f'Preparation exited with status {returncode}.',
                diagnostic=excerpt)
        if state['state'] != 'prepared':
            if state['state'] == 'stale':
                self.remove_readiness_marker(implementation)
            return dict(
                state='failed', selection=self.selection_key(resolved),
                launcher_revision=submitted_revision,
                detail='Preparation finished without a current readiness marker.',
                diagnostic=excerpt)
        try:
            _, accepted_revision = self.launcher(implementation)
        except Unsupported:
            accepted_revision = None
        if accepted_revision != submitted_revision:
            self.remove_readiness_marker(implementation)
            return dict(
                state='stale', selection=self.selection_key(resolved),
                launcher_revision=submitted_revision,
                detail='The launcher changed while accepting preparation. Resolve the selection and prepare again.',
                diagnostic=excerpt)
        return dict(
            state='prepared', selection=self.selection_key(resolved),
            launcher_revision=submitted_revision,
            detail=state['detail'], diagnostic=excerpt)

    def post_json(self, name, endpoint, body, timeout, required_key=None):
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost','127.0.0.1','::1'}:
            return dict(name=name, state='unavailable check', endpoint=endpoint,
                detail='Warm-up is restricted to selected loopback services.')
        request = Request(endpoint, method='POST', data=json.dumps(body).encode(),
            headers={'Content-Type':'application/json', 'Accept':'application/json'})
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if required_key and (not isinstance(payload, dict) or required_key not in payload):
                raise ValueError(f'response did not contain {required_key}')
            return dict(name=name, state='warmed', endpoint=endpoint,
                detail=f'Local warm-up responded with HTTP {response.status}.')
        except HTTPError as error:
            state = 'authentication required' if error.code in {401,403} else 'failed'
            error.close()
            return dict(name=name, state=state, endpoint=endpoint, detail=f'HTTP {error.code} responded.')
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as error:
            return dict(name=name, state='failed', endpoint=endpoint,
                detail=self.safe_error(error, 'Warm-up failed or timed out.'))

    @staticmethod
    def safe_error(error, fallback):
        if isinstance(error, (TimeoutError, socket.timeout)):
            return 'Timed out.'
        reason = getattr(error, 'reason', None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return 'Timed out.'
        if isinstance(error, ConnectionRefusedError) or isinstance(reason, ConnectionRefusedError):
            return 'Connection refused.'
        return fallback

    def validate_command(self,cli,command):
        """Every generated argument must still exist in the current CLI source."""
        if not command or command[0] != './run.sh':
            raise Unsupported('The generated command no longer uses the recipe launcher.')
        args=command[1:]
        flags=cli['root']
        if cli['generic'] and cli['kind'] in {'go','java'}:
            if not args or args.pop(0) not in cli['actions']:
                raise Unsupported('The generated action no longer matches the generic CLI.')
        while args:
            flag=args.pop(0)
            if cli['kind']=='python' and flag in cli['commands']:
                flags={**cli['root'],**cli['commands'][flag]}
                continue
            if flag not in flags:
                raise Unsupported(f'The CLI no longer accepts {flag}. Command generation requires an adapter update.')
            boolean=flags[flag].get('action') in {'store_true','store_false'} or flags[flag].get('type')=='Bool'
            if not boolean:
                if not args:
                    raise Unsupported(f'Missing value for {flag}.')
                argument=args.pop(0)
                if flags[flag].get('choices') and argument not in flags[flag]['choices']:
                    raise Unsupported(f'The CLI no longer accepts this value for {flag}.')
                try:
                    if flags[flag].get('arg_type') in {'int','_positive'}:
                        number=int(argument)
                        if flags[flag]['arg_type']=='_positive' and number<=0:
                            raise ValueError('positive value required')
                    elif flags[flag].get('arg_type')=='float':
                        float(argument)
                except ValueError as error:
                    raise Unsupported(f'The generated value no longer fits the CLI type for {flag}.') from error

    def local_commands(self, model, classifier):
        commands = []
        if model and model['provider']=='ollama':
            endpoint = model['endpoint'].rstrip('/')
            commands.append(dict(service='Ollama',health='curl -fsS '+shlex.quote(endpoint+'/api/tags'),
                warmup='curl -fsS --max-time 300 '+shlex.quote(endpoint+'/api/generate')+' -H '+shlex.quote('Content-Type: application/json')+' -d '+shlex.quote(json.dumps(dict(model=model['model'],prompt='Say ok.',stream=False,keep_alive='30m')))))
        if classifier and classifier['provider']=='laya':
            endpoint = urlsplit(classifier['endpoint'])
            health = urlunsplit((endpoint.scheme,endpoint.netloc,'/health','',''))
            body = dict(state='Version: 0.0.1\nRelease notes: warmup',model=classifier['model'],questions=dict(relevant=dict(
                type='choice',instructions='Is this a warmup request?',criteria=dict(yes='It is a warmup.',no='It is not a warmup.'))))
            commands.append(dict(service='Laya',health='curl -fsS '+shlex.quote(health),
                warmup='curl -fsS --max-time 600 -X POST '+shlex.quote(classifier['endpoint'])+' -H '+shlex.quote('Content-Type: application/json')+' -d '+shlex.quote(json.dumps(body))))
        return commands

    def source_path(self, relative):
        path = self.root / relative
        allowed_names = {'README.md','MATRIX_RESULTS.md','main.py','main.go',
                         'RecipeApplication.java','models.yaml','classifiers.yaml'}
        if (not self.inside(path) or path.name not in allowed_names or
            any(part.startswith('.') or part in SKIP for part in Path(relative).parts) or
            not path.is_file()):
            raise InvalidSelection('Source file is not available through this explorer.')
        if relative not in {'README.md','models.yaml','classifiers.yaml'} and not Path(relative).parts[0]=='recipes':
            raise InvalidSelection('Only recipe documentation and CLI source are available.')
        return path
