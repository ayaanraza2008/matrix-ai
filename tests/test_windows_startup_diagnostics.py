"""Unit tests for Windows startup task diagnostics and parsing."""

import subprocess
from unittest.mock import patch

from matrix_ai.access import StartupTaskManager


def test_startup_task_status_parses_ready():
    sample_output = """
Folder: \\
HostName: TEST-PC
TaskName: \\MATRIX_Logon_Startup
Next Run Time: N/A
Status: Ready
Logon Mode: Interactive only
"""
    manager = StartupTaskManager(task_name="MATRIX_Logon_Startup")
    with patch("matrix_ai.access.sys.platform", "win32"), \
         patch("matrix_ai.access.shutil.which", return_value="C:\\Windows\\System32\\schtasks.exe"), \
         patch("matrix_ai.access._run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout=sample_output, stderr=""
         )):
        st = manager.status()
        assert st["supported"] is True
        assert st["installed"] is True
        assert st["task_status"] == "Ready"
        assert "Ready" in st["detail"]


def test_startup_task_status_parses_running():
    sample_output = """
Folder: \\
HostName: TEST-PC
TaskName: \\MATRIX_Logon_Startup
Next Run Time: N/A
Status: Running
Logon Mode: Interactive only
"""
    manager = StartupTaskManager(task_name="MATRIX_Logon_Startup")
    with patch("matrix_ai.access.sys.platform", "win32"), \
         patch("matrix_ai.access.shutil.which", return_value="C:\\Windows\\System32\\schtasks.exe"), \
         patch("matrix_ai.access._run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout=sample_output, stderr=""
         )):
        st = manager.status()
        assert st["installed"] is True
        assert st["task_status"] == "Running"
        assert "Running" in st["detail"]


def test_startup_task_status_not_installed():
    manager = StartupTaskManager(task_name="MATRIX_Logon_Startup")
    with patch("matrix_ai.access.sys.platform", "win32"), \
         patch("matrix_ai.access.shutil.which", return_value="C:\\Windows\\System32\\schtasks.exe"), \
         patch("matrix_ai.access._run", return_value=subprocess.CompletedProcess(
             args=[], returncode=1, stdout="", stderr="ERROR: The system cannot find the file specified."
         )):
        st = manager.status()
        assert st["supported"] is True
        assert st["installed"] is False
        assert "not installed" in st["detail"]
