"""Copy reviewed dashboard updates into the inactive, LAB-only APP runtime."""
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import runpy

SOURCE = Path(__file__).resolve().parents[2]
TARGET = Path('/opt/cisco-lab')
FILES = (
    'action_plugins/cisco_lab_files.py',
    'library/cisco_lab_files.py',
    'roles/lab_network/tasks/install.yml',
    'network/broker/preflight.py',
    'network/broker/service.py',
    'network/security/lab_request.py',
    'roles/lab_serial/tasks/main.yml',
    'roles/lab_session/tasks/main.yml',
    'roles/lab_desktop/tasks/main.yml',
    'roles/lab_desktop/tasks/theme.yml',
    'roles/lab_firefox/tasks/main.yml',
    'tasks/settings-access.yml',
    'tasks/fresh/veyon.yml',
    'tasks/fresh/packettracer.yml',
    'files/cisco-packettracer',
    'files/cisco-lab-reboot.cfg',
    'tasks/reboot-pci.yml',
    'grub-reboot-pci.yml',
    'fresh-client.yml',
    'software-install.yml',
    'tasks/software-install.yml',
    'bootstrap-client.yml',
    'lab-baseline.yml',
    'maintenance-client.yml',
    'maintenance/teacher_wake.py',
    'maintenance/teacher_controller.py',
    'deployment/teacher/receiver.py',
    'deployment/teacher/install.py',
    'deployment/teacher/package_update.py',
    'deployment/apps/prepare_teacher_packages.py',
    'deployment/apps/prepare_teacher_access.py',
    'deployment/teacher/retire_legacy.py',
    'maintenance/controller.py',
    'maintenance/pilot.py',
    'maintenance/preview.py',
    'maintenance/reservations.py',
    'dashboard/admission.py',
    'dashboard/software.py',
    'dashboard/software_catalog.py',
    'dashboard/software_client.py',
    'dashboard/deb_client.py',
    'dashboard/own_packages.py',
    'dashboard/teacher_software.py',
    'dashboard/software.js',
    'dashboard/playbook_groups.py',
    'dashboard/playbook_groups.js',
    'dashboard/classroom.py',
    'dashboard/classroom_client.py',
    'dashboard/classroom.js',
    'dashboard/exercises.py',
    'dashboard/exercise_outputs.py',
    'dashboard/exercise_api.py',
    'dashboard/exercises.js',
    'dashboard/exercises.css',
    'dashboard/exercise_richtext.py',
    'dashboard/exercise_symbols.py',
    'dashboard/vendor/jodit/jodit.min.js',
    'dashboard/vendor/jodit/jodit.min.css',
    'dashboard/vendor/jodit/LICENSE.txt',
    'dashboard/vendor/jodit/VERSION.txt',
    'dashboard/assets/bbs-einbeck.png',
    'dashboard/activity.py',
    'dashboard/targets.py',
    'dashboard/client_report.py',
    'dashboard/auth.py',
    'dashboard/auth.css',
    'dashboard/theme.css',
    'dashboard/theme.js',
    'dashboard/room_layout.js',
    'dashboard/room_layout.css',
    'dashboard/service_access.py',
    'dashboard/presence.py',
    'dashboard/login_audit.py',
    'dashboard/operation_lock.py',
    'dashboard/pagination.py',
    'dashboard/deploy.py',
    'dashboard/intake.py',
    'dashboard/ansible_passwords.py',
    'dashboard/collect_device.py',
    'dashboard/intake.js',
    'dashboard/backup.py',
    'dashboard/provision.py',
    'dashboard/server.py',
    'dashboard/index.html',
    'dashboard/app.css',
    'dashboard/assets/cisco-lab.svg',
    'dashboard/assets/cisco-lab-icon.png',
    'dashboard/app.js',
    'dashboard/history.js',
    'dashboard/catalog.js',
    'dashboard/appearance.py',
    'dashboard/normalize_image.py',
    'dashboard/appearance_release.py',
    'dashboard/room.py',
    'dashboard/room_action.py',
    'deployment/apps/requirements.lock',
    'deployment/apps/collections.yml',
    'deployment/apps/RESTORE.txt',
    'deployment/apps/start_backup.py',
    'deployment/apps/verify_backup.py',
    'deployment/apps/check_runtime.py',
)
RETIRED_TIMER = 'maintenance/systemd/cisco-lab-maintenance.timer'
TEACHER_INSTALLER = 'deployment/teacher/teacher-installer.py'
RETIRED_TIMER_SHA256 = '0d4ab71a8dff830f3d3c17cf1b880c848442a7d3537b0d7ab024826c0fb8a47a'


def regular_root_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0:
        raise ValueError(f'LAB-Runtime-Datei nicht rootgeschützt: {path}')


def root_parents(path):
    path.relative_to(TARGET)
    if path != TARGET:
        root_parents(path.parent)
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o755)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError(f'LAB-Runtime-Verzeichnis nicht rootgeschützt: {path}')


def main():
    if os.geteuid() != 0 or TARGET.is_symlink() or TARGET.stat().st_uid != 0:
        raise ValueError('Rootgeschützte LAB-Runtime fehlt.')
    for unit in ('cisco-lab-dashboard.service', 'cisco-lab-controller.timer'):
        if subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=False).returncode == 0:
            raise ValueError(f'LAB-Dienst ist aktiv: {unit}')
    for name in FILES:
        source, target = SOURCE / name, TARGET / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'LAB-Quelldatei fehlt: {name}')
        root_parents(target.parent)
        if target.exists() or target.is_symlink():
            regular_root_file(target)
            if hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest():
                continue
        with tempfile.NamedTemporaryFile(prefix='.lab-update-', dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                with source.open('rb') as input_file:
                    shutil.copyfileobj(input_file, output)
                output.flush()
                os.fsync(output.fileno())
                os.fchmod(output.fileno(), 0o644)
                os.fchown(output.fileno(), 0, 0)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        print(f'Aktualisiert: {name}')
    retired = TARGET / RETIRED_TIMER
    if retired.exists() or retired.is_symlink():
        regular_root_file(retired)
        if hashlib.sha256(retired.read_bytes()).hexdigest() != RETIRED_TIMER_SHA256:
            raise ValueError('Alte LAB-Timervorlage weicht vom geprüften Stand ab.')
        retired.unlink()
        print(f'Entfernt: {RETIRED_TIMER}')
    teacher = TARGET / TEACHER_INSTALLER
    if teacher.exists() or teacher.is_symlink():
        regular_root_file(teacher)
        builder = runpy.run_path(str(SOURCE/'deployment/apps/prepare_teacher_access.py'))['refreshed_installer']
        content = builder(teacher.read_bytes(), SOURCE)
        if teacher.read_bytes() != content:
            with tempfile.NamedTemporaryFile(prefix='.teacher-setup-', dir=teacher.parent, delete=False) as output:
                temporary = Path(output.name)
                try:
                    output.write(content);output.flush();os.fsync(output.fileno())
                    os.fchmod(output.fileno(), 0o644);os.fchown(output.fileno(), 0, 0)
                    os.replace(temporary, teacher)
                finally:
                    temporary.unlink(missing_ok=True)
            print('Aktualisiert: '+TEACHER_INSTALLER)


if __name__ == '__main__':
    main()
