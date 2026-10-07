"""macOS Terminal handoff tests; no applications or commands are launched."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools/explorer'))

from terminal import MacTerminalLauncher, OPEN, OSASCRIPT, PBCOPY, PREFILL_SCRIPT


class Runner:
    def __init__(self, outcomes):
        self.outcomes=list(outcomes)
        self.calls=[]

    def __call__(self, args, **kwargs):
        self.calls.append((args,kwargs))
        outcome=self.outcomes.pop(0)
        if isinstance(outcome,BaseException):
            raise outcome
        return outcome


def completed(code=0,stderr=''):
    return subprocess.CompletedProcess([],code,'',stderr)


class MacTerminalLauncherTests(unittest.TestCase):
    def launcher(self,outcomes,**kwargs):
        runner=Runner(outcomes)
        launcher=MacTerminalLauncher(platform='darwin',runner=runner,
            exists=lambda path:True,timeout=2,**kwargs)
        return launcher,runner

    def test_availability_requires_macos_and_tools(self):
        self.assertFalse(MacTerminalLauncher(platform='linux',exists=lambda path:True).available)
        self.assertFalse(MacTerminalLauncher(platform='darwin',exists=lambda path:path==OSASCRIPT).available)

    def test_prefills_without_executing_or_using_a_shell(self):
        launcher,runner=self.launcher([completed()])
        result=launcher.launch("cd '/path with spaces' && command --flag")
        self.assertEqual(result['state'],'prefilled')
        args,kwargs=runner.calls[0]
        self.assertEqual(args[:3],[str(OSASCRIPT),'-e',PREFILL_SCRIPT])
        self.assertEqual(args[-1],"cd '/path with spaces' && command --flag")
        self.assertFalse(kwargs['shell'])
        self.assertNotIn('key code 36',PREFILL_SCRIPT.lower())
        self.assertNotIn('keystroke return',PREFILL_SCRIPT.lower())

    def test_permission_denial_opens_terminal_for_manual_paste(self):
        launcher,runner=self.launcher([
            completed(1,'execution error: osascript is not allowed assistive access. (-1719)'),
            completed(),
            completed()])
        result=launcher.launch('safe command')
        self.assertEqual(result['state'],'opened-copy-only')
        self.assertIn('Accessibility',result['detail'])
        self.assertEqual(runner.calls[1][0],[str(PBCOPY)])
        self.assertEqual(runner.calls[1][1]['input'],'safe command')
        self.assertEqual(runner.calls[2][0],[str(OPEN),'-a','Terminal'])
        self.assertNotIn('safe command',str(result))

    def test_timeout_uses_open_only_fallback(self):
        timeout=subprocess.TimeoutExpired(['osascript'],2)
        launcher,runner=self.launcher([timeout,completed(),completed()])
        result=launcher.launch('safe command')
        self.assertEqual(result['state'],'opened-copy-only')
        self.assertIn('timed out',result['detail'])

    def test_failed_fallback_returns_sanitized_error(self):
        launcher,runner=self.launcher([
            completed(1,'not authorized (-1743): secret command'),
            completed(1,'copy failed'),
            completed(1,'private path')])
        result=launcher.launch('secret command')
        self.assertEqual(result,dict(state='failed',
            detail='Terminal could not be opened. Use the existing copy button instead.'))


if __name__=='__main__':
    unittest.main()
