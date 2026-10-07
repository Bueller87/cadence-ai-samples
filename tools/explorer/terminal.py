"""Bounded macOS Terminal.app handoff. Commands are pasted, never executed."""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path


OSASCRIPT = Path('/usr/bin/osascript')
OPEN = Path('/usr/bin/open')
PBCOPY = Path('/usr/bin/pbcopy')
PREFILL_SCRIPT = r'''
on run argv
  if (count of argv) is not 1 then error "Expected one command."
  set commandText to item 1 of argv
  set the clipboard to commandText
  tell application "Terminal"
    activate
    do script ""
  end tell
  delay 0.3
  tell application "System Events"
    tell process "Terminal"
      set frontmost to true
      keystroke "v" using command down
    end tell
  end tell
end run
'''


class MacTerminalLauncher:
    def __init__(self, platform=None, runner=subprocess.run, exists=None, timeout=5):
        self.platform = platform or sys.platform
        self.runner = runner
        self.exists = exists or Path.is_file
        self.timeout = timeout
        self.lock = threading.Lock()

    @property
    def available(self):
        return (self.platform == 'darwin' and self.exists(OSASCRIPT)
                and self.exists(OPEN))

    def launch(self, command):
        with self.lock:
            return self._launch(command)

    def _launch(self, command):
        if not self.available:
            return dict(state='unsupported',
                detail='Automatic Terminal handoff is available only on macOS.')
        if not isinstance(command, str) or not command.strip():
            return dict(state='failed', detail='No command is available to open.')
        try:
            result = self.runner(
                [str(OSASCRIPT), '-e', PREFILL_SCRIPT, command],
                capture_output=True, text=True, timeout=self.timeout,
                check=False, shell=False)
            if result.returncode == 0:
                return dict(state='prefilled',
                    detail='Ready in Terminal. Review the command and press Enter.')
            reason = self.permission_reason(result.stderr)
        except subprocess.TimeoutExpired:
            reason = 'macOS automation timed out.'
        except OSError:
            reason = 'macOS automation could not start.'
        return self.open_only(reason, command)

    def open_only(self, reason, command):
        copied = False
        if self.exists(PBCOPY):
            try:
                copy_result = self.runner(
                    [str(PBCOPY)], input=command,
                    capture_output=True, text=True, timeout=self.timeout,
                    check=False, shell=False)
                copied = copy_result.returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                pass
        try:
            result = self.runner(
                [str(OPEN), '-a', 'Terminal'],
                capture_output=True, text=True, timeout=self.timeout,
                check=False, shell=False)
            if result.returncode == 0:
                fallback = ('Press Cmd+V to paste the copied command.' if copied else
                    'Use the existing copy button, then paste the command.')
                return dict(state='opened-copy-only',
                    detail=f'Terminal opened, but {reason} {fallback}')
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            pass
        return dict(state='failed',
            detail='Terminal could not be opened. Use the existing copy button instead.')

    @staticmethod
    def permission_reason(stderr):
        message = (stderr or '').lower()
        if '-1719' in message or 'assistive access' in message:
            return 'macOS blocked Accessibility access.'
        if '-1743' in message or 'not authorized' in message:
            return 'macOS blocked Automation access.'
        return 'macOS blocked automatic prefill.'
