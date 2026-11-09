"""Keep management jobs out of each enrolled client's evening maintenance slot."""
from datetime import datetime, time

from maintenance.preview import BERLIN


def reserved(host, moment=None):
    local = (moment or datetime.now(BERLIN)).astimezone(BERLIN)
    if local.weekday() >= 5 or not time(17, 45) <= local.time() < time(20):
        return False
    from maintenance.controller import targets
    return host in targets()
