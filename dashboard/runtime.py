"""Resolve project-local tools while retaining the Teacher installation path."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ansible_tool(name):
    if name not in {'ansible-playbook', 'ansible-inventory'}:
        raise ValueError('Unbekanntes Ansible-Werkzeug.')
    local = ROOT / '.venv/bin' / name
    return str(local if local.is_file() else Path('/usr/bin') / name)
