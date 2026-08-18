import os
from pathlib import Path
import signal
import subprocess
import sys
from unittest.mock import MagicMock, call, patch
import pytest

from manage import run_supervisor, _terminate_proc


class TestSupervisor:
    @patch("manage.subprocess.Popen")
    @patch("manage.time.sleep")
    def test_supervisor_launches_api_and_worker(self, mock_sleep, mock_popen):
        api_mock = MagicMock()
        worker_mock = MagicMock()
        mock_popen.side_effect = [api_mock, worker_mock]

        # API exits immediately to break supervisor loop
        api_mock.poll.return_value = 0
        worker_mock.poll.return_value = None

        with pytest.raises(SystemExit) as exc_info:
            run_supervisor(host="127.0.0.1", port=8001, reload=True)

        assert exc_info.value.code == 0
        assert mock_popen.call_count >= 2

        # 1. API process call args
        api_call = mock_popen.call_args_list[0]
        cmd, kwargs = api_call[0][0], api_call[1]
        assert cmd[:8] == [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001"]
        assert "--reload" in cmd
        assert "--reload-exclude" in cmd
        assert kwargs["cwd"] == str(Path(__file__).resolve().parent.parent)

        # 2. Worker process call args (must not receive uvicorn flags)
        worker_call = mock_popen.call_args_list[1]
        w_cmd, w_kwargs = worker_call[0][0], worker_call[1]
        assert w_cmd == [sys.executable, "worker.py"]
        assert "--reload" not in w_cmd
        assert w_kwargs["cwd"] == str(Path(__file__).resolve().parent.parent)

    @patch("manage.subprocess.Popen")
    @patch("manage.time.sleep")
    def test_supervisor_restarts_crashed_worker(self, mock_sleep, mock_popen):
        api_mock = MagicMock()
        worker_mock1 = MagicMock()
        worker_mock2 = MagicMock()
        mock_popen.side_effect = [api_mock, worker_mock1, worker_mock2]

        # Loop: worker crashes once, restarts, then API exits to stop loop
        api_mock.poll.side_effect = [None, None, 0]
        worker_mock1.poll.return_value = 1
        worker_mock2.poll.return_value = None

        with pytest.raises(SystemExit) as exc_info:
            run_supervisor(host="0.0.0.0", port=8001, reload=False)

        assert exc_info.value.code == 0
        # Confirms time.sleep(3.0) was called for worker restart backoff
        mock_sleep.assert_any_call(3.0)
        # Confirms worker was launched twice
        assert mock_popen.call_count == 3

    @patch("manage.subprocess.Popen")
    @patch("manage.time.sleep")
    def test_supervisor_terminates_worker_when_api_dies(self, mock_sleep, mock_popen):
        api_mock = MagicMock()
        worker_mock = MagicMock()
        mock_popen.side_effect = [api_mock, worker_mock]

        api_mock.poll.return_value = 2
        worker_mock.poll.return_value = None

        with pytest.raises(SystemExit) as exc_info:
            run_supervisor(host="0.0.0.0", port=8001)

        assert exc_info.value.code == 2
        worker_mock.terminate.assert_called_once()

    def test_terminate_proc_escalates_to_kill_on_timeout(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="test", timeout=5.0), None]

        _terminate_proc(proc, timeout=5.0)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()


class TestSystemdConfigurations:
    def test_systemd_service_unit_definitions(self):
        root_dir = Path(__file__).resolve().parent.parent.parent
        systemd_dir = root_dir / "systemd"

        api_service = (systemd_dir / "primacy-api.service").read_text()
        worker_service = (systemd_dir / "primacy-worker.service").read_text()
        target_file = (systemd_dir / "primacy.target").read_text()

        # primacy-api.service pulls in worker
        assert "Wants=primacy-worker.service" in api_service
        assert "PartOf=primacy.target" in api_service

        # primacy-worker.service binds to API
        assert "BindsTo=primacy-api.service" in worker_service
        assert "PartOf=primacy-api.service" in worker_service

        # primacy.target groups both
        assert "primacy-api.service" in target_file
        assert "primacy-worker.service" in target_file
