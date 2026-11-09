"""Prepare a reviewed local source snapshot; never publish or contact clients."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
if __package__:
    from .public_profile import load_profile, sanitize, validate_profile
else:
    from public_profile import load_profile, sanitize, validate_profile

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_TREES = {'.github', 'dashboard', 'deployment', 'maintenance', 'network', 'roles', 'tasks', 'tests', 'files',
                'action_plugins', 'library'}
PUBLIC_ROOTS = {'.gitignore', 'README.md', 'README.en.md', 'baseline-client.py', 'inventory.example.ini'}
PUBLIC_DOCS = {'dashboard.md', 'dashboard.en.md', 'wartung-vorschau.md', 'wartung-vorschau.en.md',
               'index.html', 'en.html', 'site.css', '.nojekyll', 'labornetz.svg', 'labornetz.en.svg'}
PRIVATE_PARTS = {'.git', '.migration', '.venv', '.checks', 'local-private', '__pycache__',
                 '.ssh', 'backup_data', 'node_modules'}
PRIVATE_SUFFIXES = {'.key', '.pem', '.deb', '.log', '.pyc', '.gpg', '.cred', '.known_hosts'}
PRIVATE_NAMES = {'AGENTS.md', 'HANDOVER.txt', 'Project.State.md', 'PLAN.txt', 'recovery-key',
                 'passwort', 'known_hosts', 'id_ed25519', 'id_rsa'}
LOCAL_ONLY_DEPLOYMENT = {'deployment/apps/' + name for name in (
    'prepare_oidc.py', 'retire_local_login.py', 'build_runtime.py',
    'check_teacher_setup.py', 'import_packettracer.py', 'enable_apache_route.py')}
SECRETS = (
    re.compile(rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    re.compile(rb'\$(?:6|y|2[aby])\$[A-Za-z0-9./$]{25,}'),
    re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    re.compile(rb'(?m)^\s*(?:[\w.,:@\[\]-]+\s+)?ssh-ed25519\s+AAAA[A-Za-z0-9+/=]{40,}'),
)


def public_path(name):
    path = PurePosixPath(name)
    if (name in LOCAL_ONLY_DEPLOYMENT or path.is_absolute() or '..' in path.parts or any(part in PRIVATE_PARTS for part in path.parts)
            or path.name in PRIVATE_NAMES or path.suffix in PRIVATE_SUFFIXES):
        return False
    if len(path.parts) == 1:
        return name in PUBLIC_ROOTS or path.suffix == '.yml'
    if path.parts[0] == 'docs':
        return (len(path.parts) == 2 and path.name in PUBLIC_DOCS
                or path.parts[1] == 'media' and path.suffix in {'.jpg', '.png', '.svg'})
    return path.parts[0] in PUBLIC_TREES


def public_content(name, content):
    # Keep release facts in the reviewed documentation. The exporter only
    # removes a link to the private operational history; it invents no status.
    if name == 'docs/dashboard.md':
        return content.replace(b'[Projektstatus](projektstatus.txt)', b'Projektstatus')
    return content


def validate_files(files):
    for name, content in files.items():
        if not public_path(name):
            raise ValueError('Unzulässiger öffentlicher Pfad: ' + name)
        if any(pattern.search(content) for pattern in SECRETS):
            # Never echo the matching secret value.
            raise ValueError('Mögliches Geheimnis im Export: ' + name)
        if (PurePosixPath(name).suffix not in {'.md', '.html'}
                or not (name.startswith('docs/') or PurePosixPath(name).name in {'README.md', 'README.en.md'})):
            continue
        text = content.decode()
        links = re.findall(r'(?:href|src)="([^"]+)"', text) if name.endswith('.html') else re.findall(r'\]\(([^\s)]+)\)', text)
        for link in links:
            if re.match(r'^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//|#)', link):
                continue
            value = link.split('#', 1)[0].split('?', 1)[0]
            if not value:
                continue
            parts = list(PurePosixPath(name).parent.parts)
            for part in PurePosixPath(value).parts:
                if part == '..':
                    if not parts: raise ValueError('Dokumentationslink verlässt Export: ' + name)
                    parts.pop()
                elif part != '.': parts.append(part)
            target = '/'.join(parts)
            if value.endswith('/') or value == '.': target = (target + '/index.html').lstrip('/')
            if target not in files:
                raise ValueError('Fehlender lokaler Dokumentationslink: ' + name + ' -> ' + link)


def prepare(destination, *, acceptance='not_evaluated'):
    if acceptance not in ('not_evaluated', 'pending', 'accepted'):
        raise ValueError('Ungültiger Freigabestatus für den öffentlichen Export.')
    if not destination.is_absolute(): destination = ROOT / destination
    allowed = ROOT / '.checks/public-export'
    destination = destination.resolve()
    if not destination.is_relative_to(allowed.resolve()) or destination == allowed.resolve():
        raise ValueError('Export nur in einem neuen Unterordner von .checks/public-export ablegen.')
    if destination.exists():
        raise ValueError('Exportziel existiert bereits; vorhandene Ergebnisse bleiben erhalten.')
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    files = {}
    profile = load_profile(ROOT)
    for name in sorted(set(filter(None, names))):
        if not public_path(name): continue
        source = ROOT / name
        if not stat.S_ISREG(source.lstat().st_mode) or source.resolve() != source:
            raise ValueError('Öffentliche Quelle ist keine reguläre Datei: ' + name)
        files[name] = sanitize(name, public_content(name, source.read_bytes()), profile)
    validate_files(files)
    validate_profile(files, profile)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT))
    manifest = dict(source_revision=revision, source_modified=dirty, published=False,
                    practical_fresh_client_acceptance=acceptance, files={
                        name: hashlib.sha256(content).hexdigest() for name, content in files.items()})
    destination.mkdir(mode=0o700, parents=True)
    try:
        for name, content in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o755 if (ROOT / name).stat().st_mode & 0o111 else 0o644)
        (destination / 'EXPORT-MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    except BaseException:
        shutil.rmtree(destination)
        raise
    print(f'Lokaler öffentlicher Kandidat: {destination}')
    print(f'{len(files)} Dateien; private Pfade ausgeschlossen, Geheimnis-Muster und lokale Dokumentationslinks geprüft.')
    print('Nicht veröffentlicht. Dokumentierter Praxisstatus: ' + acceptance + '.')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--acceptance', choices=('not_evaluated','pending','accepted'), default='not_evaluated',
                        help='Explizit geprüfter Praxisstatus; standardmäßig keine Behauptung zur Abnahme')
    args=parser.parse_args()
    prepare(args.destination, acceptance=args.acceptance)
