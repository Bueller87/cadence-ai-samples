"""Explorer bootstrap with an older system Python and no package downloads."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ('python3', 'python3.14', 'python3.13', 'python3.12', 'python3.11', 'python3.10')


class ExplorerLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='explorer first launch ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'tools/explorer').mkdir(parents=True)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        shutil.copy(ROOT / 'scripts/explore.sh', self.root / 'scripts/explore.sh')
        # Keep each version deterministic regardless of the test machine's PATH.
        for name in VERSIONS:
            self.executable(self.bin / name, '#!/bin/sh\nexit 1\n')
        self.env = {**os.environ, 'PATH': str(self.bin) + ':/usr/bin:/bin'}

    def executable(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755)

    def prepared_python(self, compatible=True, pyyaml_ready=True, pip_stdout_banner=False,
                        first_pip_fails=False):
        version_exit = '0' if compatible else '1'
        other_c_exit = '0' if pyyaml_ready else '1'
        pip_body = ''
        if pip_stdout_banner or first_pip_fails:
            # Pip chatter must land on stderr so server arguments stay first on stdout.
            pip_body = (
                'if [ "$1" = -m ] && [ "$2" = pip ]; then\n'
                '  state="' + str(self.root / 'pip-state') + '"\n'
                '  if [ "' + ('1' if first_pip_fails else '0') + '" = 1 ] && [ ! -f "$state" ]; then\n'
                '    touch "$state"\n'
                '    exit 1\n'
                '  fi\n'
                '  printf "Looking in indexes: https://example.invalid/simple\\n"\n'
                '  exit 0\n'
                'fi\n'
            )
        self.executable(self.root / 'tools/explorer/.venv/bin/python',
            '#!/bin/sh\n'
            'if [ "$1" = -c ]; then\n'
            '  case "$2" in *sys.version_info*) exit ' + version_exit + ';; esac\n'
            '  exit ' + other_c_exit + '\n'
            'fi\n'
            + pip_body +
            'printf "server arguments: %s %s %s\\n" "$2" "$3" "$4"\n')

    def launch(self):
        # requirements.txt is only needed when the PyYAML check fails.
        (self.root / 'tools/explorer/requirements.txt').write_text('PyYAML==6.0.2\n')
        return subprocess.run(['/bin/bash', str(self.root / 'scripts/explore.sh'),
            '--no-browser', '--port', '0'], cwd='/', env=self.env,
            text=True, capture_output=True, timeout=10)

    def test_prepared_environment_works_with_old_default_python(self):
        self.prepared_python()
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('server arguments: --no-browser --port 0', result.stdout)

    def test_fresh_environment_uses_available_versioned_python(self):
        self.prepared_python()
        interpreter = self.root / 'tools/explorer/.venv/bin/python'
        template = self.root / 'prepared-python'
        interpreter.rename(template)
        self.executable(self.bin / 'python3.12', '#!/bin/sh\n'
            'if [ "$1" = -c ]; then exit 0; fi\n'
            'if [ "$1" = -m ] && [ "$2" = venv ]; then\n'
            '  mkdir -p "$3/bin"\n'
            '  cp "' + str(template) + '" "$3/bin/python"\n'
            '  exit 0\n'
            'fi\nexit 9\n')
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Preparing the explorer', result.stderr)
        self.assertTrue(result.stdout.startswith('server arguments:'))

    def test_pip_install_noise_stays_on_stderr(self):
        self.prepared_python(pyyaml_ready=False, pip_stdout_banner=True)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('server arguments:'), result.stdout)
        self.assertIn('Looking in indexes', result.stderr)

    def test_pip_fallback_also_keeps_noise_on_stderr(self):
        self.prepared_python(pyyaml_ready=False, pip_stdout_banner=True, first_pip_fails=True)
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith('server arguments:'), result.stdout)
        self.assertIn('Looking in indexes', result.stderr)
        self.assertIn('retrying with public PyPI', result.stderr)

    def test_missing_compatible_python_explains_requirement(self):
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Python 3.10+', result.stderr)
        self.assertNotIn('server arguments:', result.stdout)

    def test_old_prepared_environment_has_recovery_instruction(self):
        self.prepared_python(compatible=False)
        result = self.launch()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Remove tools/explorer/.venv', result.stderr)


if __name__ == '__main__':
    unittest.main()
