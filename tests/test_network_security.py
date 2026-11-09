"""Hostile input and school-protection tests; no NM/Polkit calls."""
import importlib.util
import ipaddress
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('lab_request',ROOT/'network/security/lab_request.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)


class SecurityTests(unittest.TestCase):
    def identity(self):
        return {'school':{'uuid':'11111111-1111-4111-8111-111111111111','interface':'eno1','mac':'00:11:22:33:44:55','usb':False},
                'lab':{'uuid':'22222222-2222-4222-8222-222222222222','interface':'eth1','mac':'00:11:22:33:44:66','usb':True},
                'protected_subnets':['203.0.113.0/24']}
    def request(self):
        return {'action':'apply','addresses':['198.51.100.6/29','2001:db8:192::6/64'],
                'routes':[{'destination':'172.16.0.0/29','gateway':'198.51.100.1'},
                          {'destination':'2001:db8:172::/64','gateway':'2001:db8:192::1'}]}
    def test_valid_dual_stack_and_root_owned_identity(self):
        output=policy.keyfile(self.request(),self.identity())
        for value in ['interface-name=eth1','id=LAN-Labor','never-default=true','method=manual','route1=172.16.0.0/29,198.51.100.1,200']:
            self.assertIn(value,output)
        self.assertNotIn('\ngateway=',output);self.assertNotIn('\ndns=',output)
    def test_clear_has_no_ip_method_or_auto_activation(self):
        text=policy.keyfile({'action':'clear','addresses':[],'routes':[]},self.identity())
        self.assertEqual(text.count('method=disabled'),2)
        self.assertIn('autoconnect=false',text)
    def test_school_address_route_and_supernet_rejected(self):
        school=ipaddress.ip_network(self.identity()['protected_subnets'][0])
        supernet=school.supernet(new_prefix=max(0,school.prefixlen-8))
        for address in [str(school.network_address+15)+'/'+str(school.prefixlen),
                        str(supernet.network_address+1)+'/'+str(supernet.prefixlen),'198.51.100.1/0']:
            req=self.request();req['addresses']=[address]
            with self.assertRaises(ValueError):policy.validate_request(req,self.identity())
        for destination in ['0.0.0.0/0',str(school),str(supernet),'::/0']:
            req=self.request();req['routes'][0]['destination']=destination
            with self.assertRaises(ValueError):policy.validate_request(req,self.identity())

    def test_default_route_and_school_overlap_have_distinct_user_messages(self):
        from network.broker.service import user_message
        for request, expected, unexpected in [
            ({'action':'apply','addresses':['203.0.113.16/24'],'routes':[]},'203.0.113.0/24','Default'),
            ({'action':'apply','addresses':['198.51.100.6/29'],
              'routes':[{'destination':'0.0.0.0/0','gateway':'198.51.100.1'}]},'Default-Route','überschneidet')]:
            with self.assertRaises(ValueError) as caught:
                policy.validate_request(request,self.identity())
            message=user_message(caught.exception)
            self.assertIn(expected,message)
            self.assertNotIn(unexpected,message)
    def test_arbitrary_target_dns_commands_and_uuid_fields_rejected(self):
        for key in ['uuid','interface','mac','dns','command','filename','table','permissions']:
            req=self.request();req[key]='malicious'
            with self.assertRaises(ValueError):policy.validate_request(req,self.identity())
        req=self.request();req['addresses']=['198.51.100.1/24\n[connection]\nid=LAN-Schule']
        with self.assertRaises(ValueError):policy.validate_request(req,self.identity())
    def test_gateway_must_be_on_link_and_same_family(self):
        for gateway in ['203.0.113.254','198.51.100.201','::1','224.0.0.1']:
            req=self.request();req['routes'][0]['gateway']=gateway
            with self.assertRaises(ValueError):policy.validate_request(req,self.identity())
    def test_ipv6_link_local_next_hop_is_bound_to_fixed_lab_interface(self):
        req=self.request();req['routes'][1]['gateway']='fe80::1'
        text=policy.keyfile(req,self.identity())
        self.assertIn('route1=2001:db8:172::/64,fe80::1,200',text)
        req['routes'][1]['gateway']='fe80::1%eno1'
        with self.assertRaises(ValueError):policy.validate_request(req,self.identity())

    def test_route_without_matching_address_family_is_rejected(self):
        req={'action':'apply','addresses':['198.51.100.6/29'],
             'routes':[{'destination':'2001:db8:172::/64','gateway':'fe80::1'}]}
        with self.assertRaises(ValueError):policy.validate_request(req,self.identity())
        identity=self.identity();identity['protected_subnets']=[1234]
        with self.assertRaises(ValueError):policy.validate_identity(identity)

    def test_duplicate_large_and_nonobject_json_rejected(self):
        for text in ['{"action":"apply","action":"clear"}','[]',' '*16385]:
            with self.assertRaises(ValueError):policy.decode_request(text)
        req=self.request();req['addresses']*=17
        with self.assertRaises(ValueError):policy.validate_request(req,self.identity())
    def test_root_manifest_cannot_alias_school_or_fake_bus(self):
        for key in ['uuid','interface','mac']:
            identity=self.identity();identity['lab'][key]=identity['school'][key]
            with self.assertRaises(ValueError):policy.validate_request(self.request(),identity)
        identity=self.identity();identity['lab']['usb']='true'
        with self.assertRaises(ValueError):policy.validate_request(self.request(),identity)
    def test_unknown_actions_empty_apply_and_nonempty_clear_rejected(self):
        for req in [{'action':'delete-school','addresses':[],'routes':[]},
                    {'action':'apply','addresses':[],'routes':[]},
                    {'action':'clear','addresses':['198.51.100.1/24'],'routes':[]}]:
            with self.assertRaises(ValueError):policy.validate_request(req,self.identity())

if __name__=='__main__':unittest.main()
