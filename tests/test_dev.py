import signal
import subprocess

import pytest

from scripts import dev


class FakeProcess:
    def __init__(self, returncode=None, *, ignores_terminate=False):
        self.returncode = returncode
        self.ignores_terminate = ignores_terminate
        self.terminated = 0
        self.killed = 0
        self.waited = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        if not self.ignores_terminate:
            self.returncode = -signal.SIGTERM

    def kill(self):
        self.killed += 1
        self.returncode = -signal.SIGKILL

    def wait(self, timeout=None):
        self.waited += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake service", timeout or 0)
        return self.returncode


@pytest.fixture
def fake_signals(monkeypatch):
    handlers = {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL}

    def register(signum, handler):
        previous = handlers[signum]
        handlers[signum] = handler
        return previous

    monkeypatch.setattr(dev.signal, "signal", register)
    return handlers


@pytest.mark.parametrize("failed_start", [2, 4])
def test_partial_startup_failure_cleans_up_started_services(
    monkeypatch, fake_signals, failed_start
):
    started = []

    def start(*_args, **_kwargs):
        if len(started) + 1 == failed_start:
            raise OSError("cannot start service")
        process = FakeProcess()
        started.append(process)
        return process

    monkeypatch.setattr(dev.subprocess, "Popen", start)

    with pytest.raises(OSError, match="cannot start service"):
        dev.main()

    assert len(started) == failed_start - 1
    assert all(process.returncode == -signal.SIGTERM and process.waited for process in started)
    assert fake_signals == {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL}


def test_forced_shutdown_kills_and_reaps_child():
    process = FakeProcess(ignores_terminate=True)

    dev.stop_processes([process])
    dev.stop_processes([process])

    assert process.terminated == 1
    assert process.killed == 1
    assert process.returncode == -signal.SIGKILL
    assert process.waited >= 2


def test_already_exited_child_is_reaped_without_termination():
    process = FakeProcess(returncode=3)

    dev.stop_processes([process])

    assert process.terminated == 0
    assert process.killed == 0
    assert process.waited == 1


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_shuts_down_all_services(monkeypatch, fake_signals, signum):
    started = []

    def start(*_args, **_kwargs):
        process = FakeProcess()
        started.append(process)
        return process

    monkeypatch.setattr(dev.subprocess, "Popen", start)
    monkeypatch.setattr(dev.time, "sleep", lambda _seconds: fake_signals[signum](signum, None))

    dev.main()

    assert len(started) == 4
    assert all(process.returncode == -signal.SIGTERM and process.waited for process in started)
    assert fake_signals == {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL}


def test_unexpected_service_exit_reports_service_and_code(monkeypatch, fake_signals):
    started = []

    def start(*_args, **_kwargs):
        process = FakeProcess(returncode=7 if not started else None)
        started.append(process)
        return process

    monkeypatch.setattr(dev.subprocess, "Popen", start)

    with pytest.raises(SystemExit, match="mock inference.*7"):
        dev.main()

    assert all(process.waited for process in started)
    assert fake_signals == {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL}


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_during_startup_stops_launching(monkeypatch, fake_signals, signum):
    started = []

    def start(*_args, **_kwargs):
        process = FakeProcess()
        started.append(process)
        fake_signals[signum](signum, None)
        return process

    monkeypatch.setattr(dev.subprocess, "Popen", start)

    dev.main()

    assert len(started) == 1
    assert started[0].returncode == -signal.SIGTERM
    assert started[0].waited
    assert fake_signals == {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL}
