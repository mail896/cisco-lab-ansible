#!/usr/bin/python3
"""Read local sysfs only. No NetworkManager changes, network probes or SSH."""
import json
from pathlib import Path
import socket


def collect(root=Path('/sys/class/net')):
    interfaces = []
    for interface in sorted(root.iterdir()):
        if interface.name == 'lo':
            continue
        try:
            device = (interface/'device').resolve(strict=True)
            usb = next((p for p in (device,*device.parents)
                        if (p/'idVendor').is_file() and (p/'idProduct').is_file()),None)
            wireless = (interface/'wireless').exists()
            entry = {'interface':interface.name,'device_path':str(device),
                     'current_mac':(interface/'address').read_text().strip(),
                     'usb':usb is not None,'wireless':wireless,
                     'type':(interface/'type').read_text().strip(),
                     'operstate':(interface/'operstate').read_text().strip()}
            if usb:
                entry['usb_properties']={name:(usb/name).read_text().strip()
                                         for name in ('idVendor','idProduct','manufacturer','product','serial')
                                         if (usb/name).is_file()}
            interfaces.append(entry)
        except (OSError,StopIteration) as error:
            interfaces.append({'interface':interface.name,'error':str(error)})
    return interfaces


if __name__=='__main__':
    print(json.dumps({'host':socket.gethostname(),'interfaces':collect()},indent=2))
