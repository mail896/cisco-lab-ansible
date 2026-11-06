from datetime import datetime, timezone
import unittest

from maintenance.preview import PATCH_HASH, STOCK_HASH, parse_apt, plan_host, start_allowed


class MaintenancePreviewTests(unittest.TestCase):
    def test_weekdays_and_daylight_saving(self):
        for date, start_hour in (((2026, 11, 5), 17), ((2026, 7, 9), 16)):
            self.assertFalse(start_allowed(datetime(*date, start_hour - 1, 59, tzinfo=timezone.utc)))
            self.assertTrue(start_allowed(datetime(*date, start_hour, tzinfo=timezone.utc)))
            self.assertFalse(start_allowed(datetime(*date, start_hour + 2, tzinfo=timezone.utc)))
        self.assertFalse(start_allowed(datetime(2026, 11, 7, 17, tzinfo=timezone.utc)))
        with self.assertRaises(ValueError):
            start_allowed(datetime(2026, 11, 5, 18))

    def test_simulated_removal_parses_multilib_and_refuses_unknown_format(self):
        self.assertEqual(parse_apt('Inst firefox-esr [1] (2)\nRemv samba-libs:amd64 [1]\nPurg old-app [1]'),
                         {'install': ['firefox-esr'], 'remove': ['samba-libs:amd64', 'old-app']})
        with self.assertRaises(ValueError):
            parse_apt('Remv [unknown format]')

    def test_occupied_never_installs_or_shuts_down(self):
        result = plan_host(self.probe(occupied=True, upgrades='Inst winbind [1] (2)'))
        self.assertEqual(result['action'], 'download_only')
        self.assertFalse(result['autoremove_allowed'])
        self.assertFalse(result['shutdown_eligible'])

    def test_any_critical_removal_blocks_entire_autoremove(self):
        result = plan_host(self.probe(cleanup='Remv old-app [1]\nRemv veyon-service [1]'))
        self.assertEqual(result['blocked_removals'], ['veyon-service'])
        self.assertFalse(result['autoremove_allowed'])
        self.assertTrue(plan_host(self.probe(cleanup='Remv old-app [1]'))['autoremove_allowed'])

    def test_patch_and_unexpected_upgrade_removal_require_review(self):
        self.assertEqual(plan_host(self.probe(upgrades='Inst winbind:amd64 (2)'))['action'], 'review')
        self.assertEqual(plan_host(self.probe(upgrades='Inst winbind (2)', ad_hash='unknown'))['action'], 'review')
        self.assertEqual(plan_host(self.probe(upgrades='Inst winbind (2)', ad_hash=STOCK_HASH))['action'],
                         'install_if_still_free')
        self.assertEqual(plan_host(self.probe(upgrades='Remv old-app [1]'))['action'], 'review')

    def test_kernel_cleanup_preserves_running_and_one_bootable_fallback(self):
        running = 'linux-image-6.12.3-amd64'
        fallback = 'linux-image-6.12.2-amd64'
        old = 'linux-image-6.12.1-amd64'
        sample = self.probe(cleanup=f'Remv {old} [1]')
        sample.update(running_kernel=running, bootable_kernels=[running, fallback, old])
        self.assertTrue(plan_host(sample)['autoremove_allowed'])
        sample['autoremove'] = parse_apt(f'Remv {old} [1]\nRemv {fallback} [1]')
        self.assertFalse(plan_host(sample)['autoremove_allowed'])
        sample['autoremove'] = parse_apt(f'Remv {running} [1]')
        self.assertFalse(plan_host(sample)['autoremove_allowed'])
        sample['autoremove'] = parse_apt('Remv linux-image-amd64 [1]')
        self.assertFalse(plan_host(sample)['autoremove_allowed'])

    def test_unreachable_is_only_wol_candidate(self):
        result = plan_host({'error': 'timeout', 'wake_possible': True})
        self.assertEqual(result['action'], 'wol_candidate')
        self.assertFalse(result['shutdown_eligible'])
        self.assertEqual(plan_host({'error': 'host key'})['action'], 'review')

    @staticmethod
    def probe(occupied=False, upgrades='', cleanup='', ad_hash=PATCH_HASH):
        return dict(occupied=occupied, upgrade=parse_apt(upgrades), autoremove=parse_apt(cleanup),
                    ad_hash=ad_hash, reboot_required=False)


if __name__ == '__main__':
    unittest.main()
