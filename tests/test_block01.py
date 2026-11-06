"""Offline regression checks. Never access client hosts or real user homes."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'roles/lab_desktop/files' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load('prepare_desktop')
snapshot = load('snapshot')
SETTINGS = {'wallpaper': '/usr/share/backgrounds/lab.png',
            'launchers': ['applications:packettracer.desktop',
                          'applications:de.bbs-einbeck.unterrichtszeit.desktop']}


class DesktopTests(unittest.TestCase):
    def test_real_kauthorized_blocks_only_wallpaper_and_display_modules(self):
        import shutil
        include = Path('/usr/include/x86_64-linux-gnu/qt6')
        if not shutil.which('g++') or not (include/'QtCore/QCoreApplication').exists():
            self.skipTest('Qt6 Core development headers or compiler missing')
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            binary = base/'kiosk-probe'
            subprocess.run(['g++','-std=c++17','-fPIC','-I'+str(include),
                '-I'+str(include/'QtCore'),str(ROOT/'tests/kiosk_authorization_probe.cpp'),
                '-lQt6Core','-o',str(binary)],check=True,capture_output=True)
            user = base/'user'; user.mkdir()
            policy = base/'policy'; policy.mkdir()
            keys=['kcm_wallpaper','kcm_wallpaper.desktop','kcm_kscreen','kcm_kscreen.desktop']
            (user/'kdeglobals').write_text('[KDE Control Module Restrictions]\n'+
                                         '\n'.join(k+'=true' for k in keys)+'\n')
            env=dict(os.environ, HOME=str(base), XDG_CONFIG_HOME=str(user),XDG_CONFIG_DIRS=str(policy))
            before=json.loads(subprocess.check_output([str(binary)],env=env,text=True))
            self.assertTrue(all(before['controls'][k] for k in keys))
            (policy/'kdeglobals').write_text((ROOT/'roles/lab_desktop/files/kdeglobals').read_text())
            after=json.loads(subprocess.check_output([str(binary)],env=env,text=True))
            self.assertTrue(all(not after['controls'][k] for k in keys))
            for k in ['kcm_pulseaudio','kcm_networkmanagement','kcm_keyboard']:
                self.assertTrue(after['controls'][k])
            self.assertFalse(after['actions']['plasma/plasmashell/unlockedDesktop'])
            self.assertFalse(after['actions']['plasma/allow_configure_when_locked'])
            self.assertTrue(after['actions']['shell_access'])
            self.assertTrue(after['actions']['logout'])

    def test_locked_desktop_disallows_configuration_despite_personal_override(self):
        import shutil
        if not shutil.which('kreadconfig6'):
            self.skipTest('KConfig reader not installed')
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            user = base/'user'; user.mkdir()
            policy = base/'policy'; policy.mkdir()
            (user/'kdeglobals').write_text('[KDE Action Restrictions]\n'
                'plasma/plasmashell/unlockedDesktop=true\n'
                'plasma/allow_configure_when_locked=true\n')
            (policy/'kdeglobals').write_text(
                (ROOT/'roles/lab_desktop/files/kdeglobals').read_text())
            env = dict(os.environ, HOME=str(base), XDG_CONFIG_HOME=str(user),
                       XDG_CONFIG_DIRS=str(policy))
            for key in ['plasma/plasmashell/unlockedDesktop',
                        'plasma/allow_configure_when_locked']:
                value = subprocess.check_output(['kreadconfig6','--file','kdeglobals',
                    '--group','KDE Action Restrictions','--key',key], env=env, text=True)
                self.assertEqual(value.strip(), 'false')

    def test_fresh_home_has_panel_wallpaper_launchers_and_is_repeatable(self):
        text = prepare.render('', SETTINGS)
        self.assertIn('plugin=org.kde.desktopcontainment', text)
        self.assertNotIn('plugin=org.kde.plasma.folder', text)
        config = prepare.parser(text)
        plugins = [config.get(s, 'plugin', fallback='') for s in config.sections()]
        for plugin in ['org.kde.panel', 'org.kde.plasma.icontasks',
                       'org.kde.plasma.systemtray', 'org.kde.plasma.digitalclock']:
            self.assertIn(plugin, plugins)
        self.assertIn('Image=file:///usr/share/backgrounds/lab.png', text)
        self.assertIn('launchers=' + ','.join(SETTINGS['launchers']), text)
        self.assertEqual(text, prepare.render(text, SETTINGS))

    def test_existing_arbitrary_ids_multiple_desktops_and_unrelated_widgets(self):
        original = '''[Containments][43]
plugin=org.kde.plasma.folder
[Containments][81]
plugin=org.kde.desktopcontainment
[Containments][92]
plugin=org.kde.panel
[Containments][92][Applets][106]
plugin=org.kde.plasma.taskmanager
[Containments][92][Applets][106][Configuration][General]
launchers=applications:old.desktop
custom=keep
[Containments][92][Applets][411]
plugin=org.kde.plasma.digitalclock
'''
        text = prepare.render(original, SETTINGS)
        self.assertNotIn('plugin=org.kde.plasma.folder', text)
        config = prepare.parser(text)
        self.assertEqual(config.get('Containments][92][Applets][106][Configuration][General', 'custom'), 'keep')
        for desktop in (43, 81):
            self.assertEqual(config.get(f'Containments][{desktop}][Wallpaper][org.kde.image][General', 'Image'),
                             'file:///usr/share/backgrounds/lab.png')
        self.assertIn('[Containments][92][Applets][411]', text)
        self.assertNotIn('applications:old.desktop', text)
        self.assertEqual(text, prepare.render(text, SETTINGS))

    def test_missing_taskmanager_added_without_removing_existing_widgets(self):
        original = '[Containments][20]\nplugin=org.kde.panel\n[Containments][20][General]\nAppletOrder=55\n[Containments][20][Applets][55]\nplugin=org.kde.plasma.digitalclock\n'
        text = prepare.render(original, SETTINGS)
        config = prepare.parser(text)
        self.assertTrue(config.get('Containments][20][General', 'AppletOrder').startswith('55;'))
        self.assertIn('plugin=org.kde.plasma.digitalclock', text)
        self.assertEqual(text, prepare.render(text, SETTINGS))

    def test_failed_parse_does_not_damage_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'plasma-org.kde.plasma.desktop-appletsrc'
            path.write_text('not valid kconfig')
            with self.assertRaises(Exception):
                prepare.apply(Path(temporary), SETTINGS)
            self.assertEqual(path.read_text(), 'not valid kconfig')

    def test_user_backup_and_atomic_idempotent_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            path = base / 'plasma-org.kde.plasma.desktop-appletsrc'
            original = '[General]\nother=keep\n'
            path.write_text(original)
            self.assertTrue(prepare.apply(base, SETTINGS))
            self.assertFalse(prepare.apply(base, SETTINGS))
            backups = list((base / 'cisco-lab-backups/block01').iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_personal_autostart_conflict_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / 'autostart').mkdir()
            entry = base / 'autostart/lab-wallpaper.desktop'
            entry.write_text('[Desktop Entry]\nExec=old-script\n')
            with self.assertRaises(ValueError):
                prepare.check_personal_autostarts(base)
            entry.write_text('[Desktop Entry]\nHidden=true\n')
            prepare.check_personal_autostarts(base)

    def test_personal_clock_and_autostart_symlinks_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / 'elsewhere'
            target.mkdir()
            directory = base / 'autostart'
            directory.symlink_to(target)
            with self.assertRaises(ValueError):
                prepare.check_personal_autostarts(base)
            directory.unlink()
            directory.mkdir()
            clock = directory / 'lab-clock.desktop'
            clock.write_text('[Desktop Entry]\nExec=/custom/clock\n')
            with self.assertRaises(ValueError):
                prepare.check_personal_autostarts(base)
            self.assertIn('/custom/clock', clock.read_text())
            clock.unlink()
            clock.symlink_to(base / 'missing')
            with self.assertRaises(ValueError):
                prepare.check_personal_autostarts(base)

    def test_real_xdg_generator_sees_first_login_clock_and_hides_legacy(self):
        generator = Path('/usr/lib/systemd/user-generators/systemd-xdg-autostart-generator')
        if not generator.is_file():
            self.skipTest('Actual systemd XDG generator not installed')
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / 'home'
            config = home / '.config'
            config.mkdir(parents=True)
            system = base / 'system'
            (system / 'autostart').mkdir(parents=True)
            for name in prepare.AUTOSTART_NAMES[:-1]:
                (system / 'autostart' / name).write_text(
                    '[Desktop Entry]\nType=Application\nExec=/usr/bin/true\nName=Legacy\n')
            (system / 'autostart/unrelated.desktop').write_text(
                '[Desktop Entry]\nType=Application\nExec=/usr/bin/true\nName=Unrelated\n')
            templates = base / 'templates'
            templates.mkdir()
            for name in prepare.AUTOSTART_NAMES[:-1]:
                (templates / name).write_text('[Desktop Entry]\nType=Application\nHidden=true\n')
            (templates / 'lab-clock.desktop').write_text(
                '[Desktop Entry]\nType=Application\nExec=/usr/bin/true\nTryExec=/usr/bin/true\nName=Clock\nOnlyShowIn=KDE;\n')
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(config),
                       XDG_CONFIG_DIRS=str(system), SYSTEMD_LOG_LEVEL='err')
            def generate(label):
                out = base / label
                out.mkdir()
                subprocess.run([str(generator), str(out), str(out), str(out)],
                               env=env, check=True, capture_output=True)
                return {p.name for p in out.glob('*.service')}
            clock = r'app-lab\x2dclock@autostart.service'
            legacy = r'app-lab\x2dpanel\x2dfavorites@autostart.service'
            before = generate('before')
            self.assertNotIn(clock, before)
            self.assertIn(legacy, before)
            prepare.check_personal_autostarts(config)
            self.assertTrue(prepare.prepare_autostarts(config, templates))
            prepare.check_personal_autostarts(config)
            self.assertFalse(prepare.prepare_autostarts(config, templates))
            after = generate('after')
            self.assertIn(clock, after)
            self.assertNotIn(legacy, after)
            self.assertEqual(after, {clock, 'app-unrelated@autostart.service'})
            self.assertEqual((config / 'autostart/lab-clock.desktop').stat().st_mode & 0o777, 0o600)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / 'other').write_text('untouched')
            (base / 'plasma-org.kde.plasma.desktop-appletsrc').symlink_to(base / 'other')
            with self.assertRaises(ValueError):
                prepare.apply(base, SETTINGS)
            self.assertEqual((base / 'other').read_text(), 'untouched')


class SnapshotTests(unittest.TestCase):
    def test_restore_modified_missing_and_symlink_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            file = base / 'config'
            file.write_text('before')
            file.chmod(0o640)
            absent = base / 'absent'
            link = base / 'link'
            link.symlink_to('config')
            saved = snapshot.snapshot([str(file), str(absent), str(link)], base / 'backups')
            file.write_text('after')
            absent.write_text('created by deployment')
            link.unlink()
            link.write_text('changed')
            snapshot.restore(saved)
            self.assertEqual(file.read_text(), 'before')
            self.assertEqual(file.stat().st_mode & 0o777, 0o640)
            self.assertFalse(absent.exists())
            self.assertEqual(os.readlink(link), 'config')
            self.assertEqual(Path(saved).stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
