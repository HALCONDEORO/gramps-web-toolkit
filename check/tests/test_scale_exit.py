"""Scale checks must not treat a child killed by a signal as a successful run."""

import os
import signal
import subprocess
import sys

import pytest

from gwt_check import scale


@pytest.mark.parametrize(('child_exit', 'expected'), [(0, 0), (1, 0), (2, 1), (-15, 1), (-9, 1)])
def test_scale_accepts_only_success_or_findings(monkeypatch, child_exit, expected):
    monkeypatch.setattr(scale, 'COMMANDS', (['check'],))
    monkeypatch.setattr(scale.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(scale, 'run', lambda *args: ('check', 0.0, 0, child_exit, ''))

    assert scale.main(['--people', '2']) == expected


@pytest.mark.skipif(os.name != 'posix', reason='negative signal return codes are POSIX-specific')
def test_scale_fails_when_a_real_child_is_terminated(monkeypatch, capsys):
    real_run = subprocess.run
    observed = []

    def run_with_terminated_check(argv, **kwargs):
        if len(argv) > 1 and argv[1] == '-c':
            # Terminate only this newly spawned local test child, never another process.
            result = real_run([sys.executable, '-c',
                               'import os,signal; os.kill(os.getpid(), signal.SIGTERM)'], **kwargs)
            observed.append(result.returncode)
            return result
        return real_run(argv, **kwargs)

    monkeypatch.setattr(scale, 'COMMANDS', (['check'],))
    monkeypatch.setattr(scale.subprocess, 'run', run_with_terminated_check)

    assert scale.main(['--people', '2']) == 1
    assert observed == [-signal.SIGTERM]
    assert '-15' in capsys.readouterr().out


def test_scale_reports_remaining_commands_after_a_terminated_child(monkeypatch):
    calls = []

    def run_command(cmd, tree):
        calls.append((cmd[0], tree))
        rc = -signal.SIGTERM if len(calls) == 1 else 0
        return cmd[0], 0.0, 0, rc, ''

    monkeypatch.setattr(scale, 'COMMANDS', (['check'], ['repos']))
    monkeypatch.setattr(scale.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(scale, 'run', run_command)

    assert scale.main(['--people', '2', '--real', 'invented-dump.json']) == 1
    assert [command for command, _ in calls] == ['check', 'check', 'repos', 'repos']
    assert calls[1][1] == calls[3][1] == 'invented-dump.json'
