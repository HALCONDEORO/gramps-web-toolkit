"""Execute the synthetic offline demonstration under CI."""
import subprocess
import sys
from pathlib import Path


def test_demo_succeeds():
    demo = Path(__file__).with_name("demo_pagination_audit.py")
    result = subprocess.run([sys.executable, str(demo)], capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
