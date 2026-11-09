"""Fixed target validation uses the actual inventory, without shell/Ansible calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dashboard.targets import HOSTS, LAB_TARGETS, load_targets
from dashboard.deploy import LAB_TARGETS as DEPLOY_TARGETS


class TargetTests(unittest.TestCase):
    def inventory(self,rows):
        root=Path(self.enterContext(TemporaryDirectory()))
        path=root/'inventory.ini'
        path.write_text('[cisco_lab]\n'+'\n'.join(f'{host} ansible_host={address}' for host,address in rows)+'\n[cisco_lab:vars]\nansible_user=administrator\n')
        return path

    def test_runtime_consumers_share_inventory_and_inventory_order_is_irrelevant(self):
        self.assertIs(DEPLOY_TARGETS,LAB_TARGETS)
        self.assertEqual(tuple(LAB_TARGETS),HOSTS)
        rows=list(LAB_TARGETS.items())
        self.assertEqual(load_targets(self.inventory(reversed(rows))),LAB_TARGETS)
        # A reviewed address change is read from inventory, never recalculated.
        rows[0]=(HOSTS[0],LAB_TARGETS[HOSTS[1]])
        rows[1]=(HOSTS[1],LAB_TARGETS[HOSTS[0]])
        self.assertEqual(load_targets(self.inventory(rows))[HOSTS[0]],rows[0][1])

    def test_missing_duplicate_extra_and_nonlab_targets_are_rejected(self):
        rows=list(LAB_TARGETS.items())
        for invalid in (rows[:-1],rows+[rows[0]],rows+[('IServ','203.0.113.254')],
                        [(HOSTS[0],'203.0.113.254'),*rows[1:]],
                        [(HOSTS[0],rows[1][1]),*rows[1:]]):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                load_targets(self.inventory(invalid))
