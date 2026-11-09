#!/usr/bin/python3
"""Pure request validation/keyfile preparation. No root service, no nmcli calls."""
import configparser
import io
import ipaddress
import json
import re
import uuid

LIMIT = 16384


def decode_request(text):
    if len(text.encode('utf-8')) > LIMIT:
        raise ValueError('Request exceeds 16 KiB')
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result:
                raise ValueError('Duplicate JSON field: ' + key)
            result[key]=value
        return result
    value=json.loads(text,object_pairs_hook=pairs)
    if not isinstance(value,dict):
        raise ValueError('Expected JSON object')
    return value


def validate_identity(identity):
    if set(identity) != {'school','lab','protected_subnets'}:
        raise ValueError('Unexpected root manifest fields')
    for kind in ('school','lab'):
        data=identity[kind]
        if set(data) != {'uuid','interface','mac','usb'}:
            raise ValueError('Unexpected identity fields')
        uuid.UUID(data['uuid'])
        if type(data['usb']) is not bool or data['usb'] != (kind == 'lab'):
            raise ValueError('Verified USB bus roles required')
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}',data['interface']):
            raise ValueError('Invalid verified interface')
        if not re.fullmatch(r'[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}',data['mac']):
            raise ValueError('Invalid verified MAC')
    for key in ('interface','mac','uuid'):
        a,b=identity['school'][key],identity['lab'][key]
        if (uuid.UUID(a)==uuid.UUID(b) if key=='uuid' else a.lower()==b.lower()):
            raise ValueError('School and laboratory identities must differ')
    if not isinstance(identity['protected_subnets'],list) or not all(isinstance(n,str) for n in identity['protected_subnets']):
        raise ValueError('Protected subnets must be a list of CIDR strings')
    protected=[ipaddress.ip_network(n,strict=True) for n in identity['protected_subnets']]
    if not protected:
        raise ValueError('Protected school subnets required')
    return protected


def validate_request(request,identity):
    protected=validate_identity(identity)
    if set(request) != {'action','addresses','routes'} or request['action'] not in ('apply','clear'):
        raise ValueError('Only apply/clear with addresses/routes is permitted')
    addresses,routes=request['addresses'],request['routes']
    if not isinstance(addresses,list) or len(addresses)>16 or not all(isinstance(a,str) for a in addresses):
        raise ValueError('At most 16 address strings allowed')
    if not isinstance(routes,list) or len(routes)>32:
        raise ValueError('At most 32 routes allowed')
    if request['action']=='clear' and (addresses or routes):
        raise ValueError('Clear must not carry configuration')
    if request['action']=='apply' and not addresses:
        raise ValueError('Apply needs at least one manual address')
    def allowed(network):
        if network.prefixlen==0:
            raise ValueError('Default routes are forbidden')
        for subnet in protected:
            if subnet.version==network.version and network.overlaps(subnet):
                raise ValueError('Address or route overlaps school subnet: '+str(subnet))
    normalized=[]
    for value in addresses:
        address=ipaddress.ip_interface(value)
        if '%' in str(address) or address.ip.is_unspecified or address.ip.is_multicast or address.ip.is_loopback:
            raise ValueError('Invalid exercise address')
        allowed(address.network)
        normalized.append(str(address))
    if len(set(normalized))!=len(normalized):
        raise ValueError('Duplicate address')
    clean_routes=[]
    for route in routes:
        if not isinstance(route,dict) or set(route)!={'destination','gateway'}:
            raise ValueError('Only destination/gateway supported per route')
        if not all(isinstance(route[key],str) for key in ('destination','gateway')):
            raise ValueError('Route destination and gateway must be strings')
        network=ipaddress.ip_network(route['destination'],strict=True)
        gateway=ipaddress.ip_address(route['gateway'])
        allowed(network)
        if network.version not in {ipaddress.ip_interface(a).version for a in normalized}:
            raise ValueError('Routes need a manually configured address of the same IP version')
        if gateway.version!=network.version or gateway.is_unspecified or gateway.is_multicast or gateway.is_loopback or '%' in str(gateway):
            raise ValueError('Invalid next hop')
        if not (gateway.version==6 and gateway.is_link_local) and not any(gateway in ipaddress.ip_interface(a).network for a in normalized
                   if ipaddress.ip_interface(a).version==gateway.version):
            raise ValueError('Gateway must be on a configured directly connected laboratory subnet')
        clean_routes.append({'destination':str(network),'gateway':str(gateway)})
    return {'action':request['action'],'addresses':normalized,'routes':clean_routes}


def keyfile(request,identity):
    """Generate root-owned profile content, never caller-selected profile names."""
    request=validate_request(request,identity)
    lab=identity['lab']
    config=configparser.ConfigParser(interpolation=None);config.optionxform=str
    config['connection']={'id':'LAN-Labor','uuid':str(uuid.UUID(lab['uuid'])),
                          'type':'ethernet','interface-name':lab['interface'],'autoconnect':'false'}
    config['ethernet']={'mac-address':lab['mac'].lower()}
    for version in (4,6):
        addresses=[a for a in request['addresses'] if ipaddress.ip_interface(a).version==version]
        values={'method':'manual' if addresses else 'disabled','never-default':'true',
                'ignore-auto-dns':'true','ignore-auto-routes':'true','route-metric':'200'}
        for i,address in enumerate(addresses,1):values[f'address{i}']=address
        for i,route in enumerate((r for r in request['routes'] if ipaddress.ip_network(r['destination']).version==version),1):
            values[f'route{i}']=route['destination']+','+route['gateway']+',200'
        config[f'ipv{version}']=values
    buffer=io.StringIO();config.write(buffer,space_around_delimiters=False)
    return buffer.getvalue()
