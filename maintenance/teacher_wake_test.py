"""Explicit, attended two-minute suspend/wake test on LAB-Teacher only."""
from contextlib import contextmanager
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import subprocess
import time
import uuid

UNIT = 'cisco-lab-teacher-wake-test'
LOCK = Path('/home/marc/.local/state/cisco-lab-dashboard/operation.lock')
REPORT_DIRECTORY = Path('/var/lib/cisco-lab-teacher-wake')


def read(*command):
    return subprocess.check_output(command, text=True, timeout=15).strip()


def clock_sample():
    return dict(wall=time.time(), boot=time.clock_gettime(time.CLOCK_BOOTTIME),
                monotonic=time.monotonic())


def classify(before, after, triggered):
    elapsed = after['boot'] - before['boot']
    suspended = elapsed - (after['monotonic'] - before['monotonic'])
    # A running machine reaching the alarm, or an early keyboard wake, is not a pass.
    return dict(elapsed_seconds=round(elapsed, 2), suspended_seconds=round(suspended, 2),
                timer_triggered=triggered,
                passed=bool(triggered and 90 <= suspended <= elapsed <= 180))


@contextmanager
def management_guard():
    descriptor = os.open(LOCK, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as file:
        info = os.fstat(file.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != pwd.getpwnam('marc').pw_uid
                or info.st_mode & 0o077):
            raise RuntimeError('Private Verwaltungssperre ist nicht eindeutig.')
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Verwaltungsauftrag läuft. Kein Standby-Test.') from error
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def cycle():
    for unit in (UNIT + '.timer', UNIT + '.service'):
        if read('systemctl', 'show', unit, '-p', 'LoadState', '--value') != 'not-found':
            raise RuntimeError('Ein früherer Aufwachtest ist noch geladen. Kein Eingriff.')
    for unit in ('apt-daily.service', 'apt-daily-upgrade.service',
                 'cisco-lab-maintenance.service', 'cisco-lab-controller.service'):
        if read('systemctl', 'show', unit, '-p', 'ActiveState', '--value') not in ('inactive', 'failed'):
            raise RuntimeError(unit + ' läuft. Kein Standby-Test.')
    created = False
    before = clock_sample()
    try:
        subprocess.run([
            'systemd-run', '--quiet', '--collect', '--unit=' + UNIT,
            '--on-active=120s', '--timer-property=WakeSystem=true',
            '--timer-property=AccuracySec=1s', '/usr/bin/logger',
            '--tag=' + UNIT, 'Zwei-Minuten-Aufwachtest: Alarm erreicht.',
        ], check=True, timeout=15)
        created = True
        if (read('systemctl', 'show', UNIT + '.timer', '-p', 'ActiveState', '--value') != 'active'
                or read('systemctl', 'show', UNIT + '.timer', '-p', 'WakeSystem', '--value') != 'yes'):
            raise RuntimeError('Weckfähiger Testtimer nicht aktiv. Kein Suspend.')
        print('Standby wird angefordert. Bis zur automatischen Rückkehr nicht bedienen.', flush=True)
        subprocess.run(['systemctl', 'suspend'], check=True, timeout=30)
        # systemctl suspend returns after enqueueing. Measure the actual suspended interval.
        while True:
            after = clock_sample()
            elapsed = after['boot'] - before['boot']
            slept = elapsed - (after['monotonic'] - before['monotonic'])
            if slept >= 5 or elapsed > 30:
                break
            time.sleep(1)
        for _ in range(15):
            last = read('systemctl', 'show', UNIT + '.timer', '-p', 'LastTriggerUSec', '--value')
            if last not in ('', 'n/a'):
                break
            time.sleep(1)
        report = classify(before, after, last not in ('', 'n/a'))
        report.update(time=datetime.now(timezone.utc).isoformat(),
                      boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                      limitation='Standby-Test; kein Nachweis für Einschalten aus S5.')
        return report
    finally:
        if created:
            subprocess.run(['systemctl', 'stop', UNIT + '.timer', UNIT + '.service'],
                           check=True, timeout=20)


def save_report(report):
    REPORT_DIRECTORY.mkdir(mode=0o755, exist_ok=True)
    info = REPORT_DIRECTORY.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Testbericht-Verzeichnis ist nicht rootgeschützt.')
    temporary = REPORT_DIRECTORY / (uuid.uuid4().hex + '.next')
    with temporary.open('x') as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
    temporary.chmod(0o644)
    temporary.replace(REPORT_DIRECTORY / 'last-test.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suspend-test', action='store_true',
                        help='Explicitly request the agreed suspend test with a recovery route available')
    args = parser.parse_args()
    if not args.suspend_test:
        parser.error('Standby nur mit --suspend-test nach vereinbartem Testsignal.')
    if os.geteuid() != 0 or socket.gethostname() != 'LAB-Teacher':
        raise SystemExit('Nur als root auf LAB-Teacher und erst nach vereinbartem Testsignal.')
    with management_guard():
        report = cycle()
        save_report(report)
    print(json.dumps(report, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit('Aufwachtest nicht bestanden; keine automatische Freigabe.')


if __name__ == '__main__':
    main()
