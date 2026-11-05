"""Pure form conversion; the privileged broker independently validates all input."""
def to_request(address_text, route_text):
    addresses=[line.strip() for line in address_text.splitlines() if line.strip()]
    routes=[]
    for line in route_text.splitlines():
        if not line.strip(): continue
        parts=line.split()
        if len(parts)!=3 or parts[1]!='via':
            raise ValueError('Route als Zielnetz/Praefix via Routeradresse eingeben.')
        routes.append({'destination':parts[0],'gateway':parts[2]})
    return {'action':'apply','addresses':addresses,'routes':routes}


def from_configuration(configuration):
    return ('\n'.join(configuration['addresses']),
            '\n'.join(r['destination']+' via '+r['gateway'] for r in configuration['routes']))
