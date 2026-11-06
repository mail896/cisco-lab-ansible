"""Offline session-policy tests: temporary config trees, mocked X11 commands."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / 'roles/lab_session/files'


def load(name):
    spec = importlib.util.spec_from_file_location(name, FILES / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load('session_runtime')
preflight = load('preflight')


class SessionTests(unittest.TestCase):
    def test_first_login_prepares_runtime_and_suppresses_legacy_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            user = base/'config'
            policies = base/'policies'; policies.mkdir()
            shutil.copyfile(FILES/'session-runtime.desktop', policies/'lab-session-runtime.desktop')
            for name in runtime.AUTOSTART_NAMES[1:]:
                shutil.copyfile(FILES/'disabled.desktop', policies/name)
            runtime.prepare_autostarts(user, policies)
            self.assertIn('Exec=/usr/local/libexec/cisco-lab/session-runtime',
                          (user/'autostart/lab-session-runtime.desktop').read_text())
            for name in runtime.AUTOSTART_NAMES[1:]:
                self.assertIn('Hidden=true', (user/'autostart'/name).read_text())
            before = {p.name:p.stat().st_mtime_ns for p in (user/'autostart').iterdir()}
            runtime.prepare_autostarts(user, policies)
            self.assertEqual(before, {p.name:p.stat().st_mtime_ns for p in (user/'autostart').iterdir()})
            foreign = user/'autostart/monitor75.desktop'
            foreign.write_text('[Desktop Entry]\nType=Application\nExec=unmanaged\n')
            with self.assertRaises(ValueError):
                runtime.prepare_autostarts(user, policies)
            self.assertIn('Exec=unmanaged', foreign.read_text())

    def test_autostart_redirects_refused_without_writing_outside_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            outside = base/'outside'; outside.mkdir()
            user = base/'config'; user.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                runtime.prepare_autostarts(user, base/'unused')
            self.assertEqual(list(outside.iterdir()), [])

    def test_supported_rate_on_actual_output_current_resolution(self):
        text = '''DP-3 connected primary 2560x1440+0+0
   2560x1440  59.95*+ 74.97
   1920x1080  75.00
HDMI-9 disconnected
   2560x1440  75.00
'''
        self.assertEqual(runtime.monitor_commands(text),
                         [['/usr/bin/xrandr','--output','DP-3','--mode','2560x1440','--rate','74.97']])

    def test_unsupported_already_correct_and_inactive_outputs_unchanged(self):
        for text in ['HDMI-1 connected\n  1920x1080 60.00*+ 144.00\n',
                     'DP-1 connected\n  2560x1440 74.97*+ 60.00\n',
                     'DP-1 connected\n  2560x1440 75.00 60.00\n']:
            self.assertEqual(runtime.monitor_commands(text), [])

    def test_multiple_screens_and_preferred_is_not_current(self):
        text = 'DP-1 connected\n  1920x1080 60.00* 75.00+\nDP-2 connected\n  1280x1024 60.00*+ 75.02\n'
        commands = runtime.monitor_commands(text)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[1][-1], '75.02')

    def test_runtime_uses_inherited_display_and_no_shell(self):
        calls = []
        def run(args, **kwargs):
            self.assertNotIn('shell',kwargs)
            calls.append(args)
            return subprocess.CompletedProcess(args,0,'DP-1 connected\n  1920x1080 60.00* 75.00\n','')
        runtime.initialize(run=run, env={'XDG_SESSION_TYPE':'x11','DISPLAY':':7'})
        self.assertIn(['/usr/bin/numlockx','on'], calls)
        self.assertIn(['/usr/bin/xset','-dpms'], calls)
        self.assertEqual(calls[-1][-1], '75.00')
        self.assertNotIn(':0', str(calls))

    def test_wayland_rejected_and_xrandr_failure_does_not_change_mode(self):
        with self.assertRaises(ValueError):
            runtime.initialize(env={'XDG_SESSION_TYPE':'wayland','DISPLAY':':0'})
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args,1 if '--query' in args else 0,'','query unavailable')
        with self.assertRaises(ValueError):
            runtime.initialize(run=run,env={'XDG_SESSION_TYPE':'x11','DISPLAY':':9'})
        self.assertFalse(any('--output' in c for c in calls))

    def test_sddm_high_priority_conflicts_fail_instead_of_silent_override(self):
        managed = (FILES/'sddm-session.conf').read_text()
        self.assertEqual(preflight.conflicting_sddm('[General]\nNumlock=off\n',managed), ['General/Numlock'])
        self.assertEqual(preflight.conflicting_sddm('[Theme]\nCurrent=breeze\n',managed), [])
        self.assertEqual(preflight.conflicting_sddm('[General]\nNumlock=on\n',managed), [])

    @unittest.skipUnless(shutil.which('kreadconfig6'), 'KConfig reader not installed locally')
    def test_real_kconfig_immutable_values_override_old_user_preferences(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            user = base/'user'; user.mkdir()
            policy = base/'session'; policy.mkdir()
            desktop = base/'desktop'; desktop.mkdir()
            (user/'ksmserverrc').write_text('[General]\nconfirmLogout=false\nloginMode=restorePreviousLogout\n')
            (user/'kscreenlockerrc').write_text('[Daemon]\nAutolock=true\n')
            (user/'powerdevilrc').write_text('[AC][Display]\nDimDisplayWhenIdle=true\nTurnOffDisplayWhenIdle=true\n')
            for name in ('ksmserverrc','kscreenlockerrc','powerdevilrc','kwinrc','kcminputrc','kdeglobals'):
                shutil.copyfile(FILES/name,policy/name)
            shutil.copyfile(ROOT/'roles/lab_desktop/files/kdeglobals',desktop/'kdeglobals')
            env=os.environ.copy();env.update(HOME=str(base),XDG_CONFIG_HOME=str(user),XDG_CONFIG_DIRS=f'{policy}:{desktop}')
            def read(file,groups,key):
                command=['kreadconfig6','--file',file]
                for group in groups: command += ['--group',group]
                command += ['--key',key]
                result=subprocess.run(command,env=env,capture_output=True,text=True,check=True)
                return result.stdout.strip()
            self.assertEqual(read('ksmserverrc',['General'],'confirmLogout'),'true')
            self.assertEqual(read('ksmserverrc',['General'],'loginMode'),'emptySession')
            self.assertEqual(read('kscreenlockerrc',['Daemon'],'Autolock'),'false')
            self.assertEqual(read('powerdevilrc',['AC','Display'],'DimDisplayWhenIdle'),'false')
            self.assertEqual(read('powerdevilrc',['AC','Display'],'TurnOffDisplayWhenIdle'),'false')
            self.assertEqual(read('kwinrc',['Plugins'],'wobblywindowsEnabled'),'true')
            self.assertEqual(read('kcminputrc',['Keyboard'],'NumLock'),'0')
            self.assertEqual(read('kdeglobals',['KDE Action Restrictions'],'plasma/plasmashell/unlockedDesktop'),'false')
            self.assertEqual(read('kdeglobals',['KDE Action Restrictions'],'plasma/allow_configure_when_locked'),'false')
            self.assertIn('confirmLogout=false',(user/'ksmserverrc').read_text())

    def test_hooks_do_not_exit_or_restart_session_and_use_only_scoped_policy(self):
        hook=(FILES/'30-lab-session.sh').read_text()
        self.assertIn('/etc/cisco-lab/session-xdg',hook)
        self.assertNotIn('exit ', '\n'.join(l for l in hook.splitlines() if not l.startswith('#')))
        setup=(FILES/'sddm-display-setup.sh').read_text()
        self.assertNotIn('DISPLAY=',setup)
        self.assertNotIn('systemctl',setup)


if __name__ == '__main__':
    unittest.main()
