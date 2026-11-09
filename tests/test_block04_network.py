"""Offline policy and network-plan checks, no browser or NM modification."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('network_plan',ROOT/'network/plan.py')
planner = importlib.util.module_from_spec(spec); spec.loader.exec_module(planner)


class PolicyTests(unittest.TestCase):
    def test_firefox_migration_refuses_unrelated_enterprise_settings(self):
        spec=importlib.util.spec_from_file_location('firefox_preflight',
            ROOT/'roles/lab_firefox/files/preflight.py')
        guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
        planned=json.loads((ROOT/'roles/lab_firefox/files/policies.json').read_text())
        old=copy.deepcopy(planned)
        old['policies']['PromptForDownloadLocation']=False
        old['policies']['Preferences'].update({
            'startup.homepage_welcome_url': {'Value': '', 'Status': 'locked'},
            'startup.homepage_welcome_url.additional': {'Value': '', 'Status': 'locked'},
            'identity.fxaccounts.enabled': {'Value': False, 'Status': 'locked'},
        })
        self.assertEqual(guard.unknown_settings(old,planned),[])
        old['policies']['Certificates']={'ImportEnterpriseRoots':True}
        old['policies']['Preferences']['school.integration']={'Value':True,'Status':'locked'}
        self.assertEqual(set(guard.unknown_settings(old,planned)),
                         {'/policies/Certificates','/policies/Preferences/school.integration'})
        old['policies']['Preferences']['startup.homepage_welcome_url']['Value']='https://example.invalid'
        self.assertIn('/policies/Preferences/startup.homepage_welcome_url',
                      guard.unknown_settings(old,planned))

    def test_interview_policies(self):
        p=json.loads((ROOT/'roles/lab_firefox/files/policies.json').read_text())['policies']
        self.assertEqual(p['Homepage'],{'URL':'https://school.example.org','Locked':True,'StartPage':'homepage'})
        for key in ('PasswordManagerEnabled','OfferToSaveLogins','NewTabPage'):
            self.assertFalse(p[key])
        self.assertTrue(p['DisableFirefoxAccounts'])
        self.assertTrue(p['PromptForDownloadLocation'])
        self.assertEqual(p['Preferences']['browser.newtabpage.enabled'],{'Value':False,'Status':'locked'})

    def facts(self):
        return {'host':'LAB-08A',
                'school':{'interface':'eno1','mac':'00:11:22:33:44:55','uuid':'11111111-1111-4111-8111-111111111111','usb':False},
                'lab':{'interface':'enx001122334466','mac':'00:11:22:33:44:66','uuid':'22222222-2222-4222-8222-222222222222','usb':True}}

    def test_network_plan_is_data_only_and_preserves_verified_identity(self):
        facts=self.facts(); original=copy.deepcopy(facts)
        plan=planner.build_plan(facts)
        self.assertFalse(plan['executable']);self.assertTrue(plan['blockers'])
        self.assertEqual(facts,original)

    def test_comparison_host_wrong_bus_and_shared_identity_rejected(self):
        for change in ('host','bus','uuid','mac','interface'):
            facts=self.facts()
            if change=='host':facts['host']='LAB-08B'
            elif change=='bus':facts['lab']['usb']=False
            else:facts['lab'][change]=facts['school'][change]
            with self.assertRaises(ValueError):planner.build_plan(facts)

    def test_no_unverified_or_ambiguous_device_identifiers(self):
        for key,value in [('uuid','LAN-Labor'),('mac','unknown'),('interface','eno1; reboot')]:
            facts=self.facts();facts['lab'][key]=value
            with self.assertRaises(ValueError):planner.build_plan(facts)


class InterfaceTests(unittest.TestCase):
    def test_usb_bus_identity_is_independent_of_interface_name(self):
        import tempfile
        spec=importlib.util.spec_from_file_location('collect_interfaces',ROOT/'network/collect_interfaces.py')
        collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);net=base/'net';net.mkdir();usb=base/'usb';usb.mkdir();pci=base/'pci';pci.mkdir()
            (usb/'idVendor').write_text('1234');(usb/'idProduct').write_text('5678')
            for name,parent in [('eth1',usb),('enp0s20f0u1',usb),('enx_internal',pci)]:
                d=net/name;d.mkdir();(d/'device').symlink_to(parent)
                for key,value in [('address','00:11:22:33:44:55'),('type','1'),('operstate','down')]:
                    (d/key).write_text(value)
            result={r['interface']:r for r in collector.collect(net)}
            self.assertTrue(result['eth1']['usb']);self.assertTrue(result['enp0s20f0u1']['usb'])
            self.assertFalse(result['enx_internal']['usb'])

if __name__=='__main__':unittest.main()
