"""Persist the operator-verified Bash editing mode and Readline paste setting."""
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import tempfile

BEGIN = '# BEGIN CISCO-LAB SSH TERMINAL'
END = '# END CISCO-LAB SSH TERMINAL'
BLOCK = r'''# BEGIN CISCO-LAB SSH TERMINAL
# Aktiviere den am echten Mac-Terminal bestätigten Eingabemodus.
if [[ -n ${SSH_TTY:-} && -t 0 && -t 1 && ${TERM:-dumb} != dumb ]]; then
    set -o emacs
    bind 'set enable-bracketed-paste on'
fi
# END CISCO-LAB SSH TERMINAL
'''


def bashrc_content(text):
    text = re.sub(re.escape(BEGIN) + r'.*?' + re.escape(END) + r'\n?', '', text, flags=re.S)
    lines = [line for line in text.splitlines()
             if not re.fullmatch(r'''\s*bind\s+(['"])set enable-bracketed-paste on\1\s*''', line)]
    return '\n'.join(lines).rstrip() + '\n\n' + BLOCK


def main():
    if os.geteuid() != 0 or socket.gethostname().split('.')[0] != 'LAB-Teacher':
        raise SystemExit('Nur mit root auf LAB-Teacher ausführen.')
    account = pwd.getpwnam('marc')
    home = Path(account.pw_dir)
    shell = home / '.bashrc'
    readline = home / '.inputrc'
    for path in (shell, readline):
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid:
                raise ValueError('Unerwartete Benutzerdatei: ' + str(path))
    old_shell = shell.read_text()
    old_readline = readline.read_text() if readline.exists() else None
    new_readline = old_readline if old_readline is not None else '$include /etc/inputrc\n'
    new_readline = re.sub(r'(?m)^\s*set enable-bracketed-paste\s+(?:on|off)\s*$', '', new_readline)
    new_readline = new_readline.rstrip() + '\nset enable-bracketed-paste on\n'
    changes = {shell: bashrc_content(old_shell), readline: new_readline}
    backup = Path('/var/backups/cisco-lab-terminal-paste.before')
    backup.mkdir(mode=0o700, exist_ok=True)
    for path, text in changes.items():
        if path.exists() and not (backup / path.name).exists():
            with (backup / path.name).open('xb') as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(path.read_bytes())
        if path.exists() and path.read_text() == text:
            continue
        with tempfile.NamedTemporaryFile(mode='w', dir=home, delete=False) as f:
            temporary = Path(f.name)
            try:
                os.fchmod(f.fileno(), stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644)
                os.fchown(f.fileno(), account.pw_uid, account.pw_gid)
                f.write(text); f.flush(); os.fsync(f.fileno())
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    print('SSH-Terminalinitialisierung für marc dauerhaft eingerichtet. Bestehende Sitzung unverändert.')


if __name__ == '__main__':
    main()
