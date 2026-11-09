"""Project-local Ansible entry point consuming inherited anonymous memory files.

Ansible 2.19 canonicalizes password-file CLI arguments, breaking /proc/self/fd.
Override the CLI's password hook in this process only; close the descriptors
before Ansible forks workers. No installed Ansible code is changed.
"""
import os
from pathlib import Path
import stat
import sys


def command(mode, descriptors, arguments):
    if mode not in ('adhoc', 'playbook') or not 1 <= len(descriptors) <= 2:
        raise ValueError('Unbekannter Ansible-Passwortauftrag.')
    ssh, sudo = descriptors[0], descriptors[1] if len(descriptors) == 2 else -1
    return [sys.executable, '-IB', str(Path(__file__).resolve()), mode, str(ssh), str(sudo), *arguments]


def read_password(fd):
    if fd == -1:
        return None
    try:
        info = os.fstat(fd)
        if (fd < 3 or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or not 0 < info.st_size <= 256):
            raise ValueError('Ungültige anonyme Anmeldedaten.')
        value = os.pread(fd, 257, 0).decode()
        if value != value.strip() or any(char in value for char in '\r\n\0'):
            raise ValueError('Ungültige anonyme Anmeldedaten.')
        return value
    finally:
        os.close(fd)


def main():
    mode, ssh_fd, sudo_fd = sys.argv[1:4]
    passwords = read_password(int(ssh_fd)), read_password(int(sudo_fd))
    if mode == 'adhoc':
        from ansible.cli.adhoc import AdHocCLI as BaseCLI
        name = 'ansible'
    elif mode == 'playbook':
        from ansible.cli.playbook import PlaybookCLI as BaseCLI
        name = 'ansible-playbook'
    else:
        raise ValueError('Unbekannter Ansible-Auftrag.')

    class MemoryPasswordsCLI(BaseCLI):
        @staticmethod
        def ask_passwords():
            return passwords

    # The SSH plugin locates its askpass helper beside sys.argv[0]. This wrapper
    # lives outside the venv's bin directory; systemd's PATH has no Ansible.
    # Preserve the real CLI location, so both ad-hoc and playbook password
    # authentication use the installed helper without altering the global PATH.
    entry = Path(sys.executable).with_name(name)
    if not entry.is_file():
        raise ValueError('Projektlokaler Ansible-Einstieg fehlt.')
    sys.argv = [str(entry), *sys.argv[4:]]
    MemoryPasswordsCLI.cli_executor(sys.argv)


if __name__ == '__main__':
    main()
