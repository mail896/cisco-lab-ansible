"""Offline migration/rollback checks; injected commands never touch real units."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml
from network.broker.rollback import rollback
from network.security.native_keyfile import verify
from network.security.lab_request import keyfile
from test_network_broker import IDENTITY,APPLY,CLEAR
ROOT=Path(__file__).resolve().parents[1]


class MigrationTests(unittest.TestCase):
    def test_native_nm_accepts_neutral_and_dualstack_linklocal_profiles(self):
        verify(keyfile(CLEAR,IDENTITY));verify(keyfile(APPLY,IDENTITY))
        req={'action':'apply','addresses':['2001:db8:192::6/64'],
             'routes':[{'destination':'2001:db8:172::/64','gateway':'fe80::1'}]}
        verify(keyfile(req,IDENTITY))
        with self.assertRaises(ValueError):verify('[connection]\ntype=invalid-type\n')

    def fixture(self,base):
        (base/'source/role/files').mkdir(parents=True)
        marker=base/'restored'
        # The restore function is a stub writing only inside this temporary tree.
        (base/'source/role/files/snapshot.py').write_text('from pathlib import Path\ndef restore(directory):\n    (Path(directory)/"restored").write_text("yes")\n')
        (base/'files.json').write_text('[]')
        (base/'settings.json').write_text(json.dumps({'manifest':{'identity':IDENTITY},'verified':{'active_lab':False}}))
        units=[{'item':'lab-network-heal.timer','stdout':'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n'},
               {'item':'lab-school-network-heal.timer','stdout':'LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\n'},
               {'item':'cisco-lab-network.service','stdout':'LoadState=not-found\n'}]
        (base/'units.json').write_text(json.dumps(units))
        return marker

    def test_rollback_restores_only_previously_enabled_active_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);marker=self.fixture(base);calls=[]
            def run(args):
                calls.append(args)
                if 'show' in args:return 'LoadState=loaded\n'
                if '--active' in args:return IDENTITY['lab']['uuid']+'\n'
                return ''
            result=rollback(base,run,lambda p:p.read_text())
            self.assertTrue(result['ok']);self.assertTrue(marker.exists())
            self.assertIn(['/usr/bin/systemctl','enable','lab-network-heal.timer'],calls)
            self.assertNotIn(['/usr/bin/systemctl','start','lab-school-network-heal.timer'],calls)
            self.assertNotIn(['/usr/bin/systemctl','start','cisco-lab-network.service'],calls)

    def test_incomplete_restore_and_unexpected_unit_make_no_system_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);self.fixture(base);calls=[]
            (base/'units.json').write_text(json.dumps([{'item':'ssh.service','stdout':'LoadState=loaded'}]))
            with self.assertRaises(ValueError):rollback(base,lambda args:calls.append(args),lambda p:p.read_text())
            self.assertEqual(calls,[])
            (base/'source/role/files/snapshot.py').unlink()
            with self.assertRaises(FileNotFoundError):rollback(base,lambda args:calls.append(args),lambda p:p.read_text())
            self.assertEqual(calls,[])

    def test_shared_configuration_directory_stays_readable_for_blocks_1_to_4(self):
        tasks=yaml.safe_load((ROOT/'roles/lab_network/tasks/install.yml').read_text())
        outer=next(t for t in tasks if 'block' in t and t['name'].startswith('Weitere'))
        private=next(t for t in outer['block'] if t['name']=='Verwaltungsverzeichnisse bereitstellen')
        self.assertNotIn('/etc/cisco-lab',private['loop'])
        public=next(t for t in outer['block'] if t['name'].startswith('Gemeinsamen Konfigurationspfad'))
        self.assertEqual(public['ansible.builtin.file']['mode'],'0755')
        self.assertIn('rescue',outer)

if __name__=='__main__':unittest.main()
