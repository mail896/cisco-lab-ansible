"""Bounded read-only history pages, without changing legacy API responses."""
from collections import deque

PAGE_SIZE = 10


def paginate(data, number, key='runs', *, fresh=False):
    if number is None:
        return data
    if type(number) is not int or not 1 <= number <= 999999:
        raise ValueError('Ungültige Seitennummer.')
    rows = data[key]
    start = (number - 1) * PAGE_SIZE
    selected, tail = [], deque(maxlen=PAGE_SIZE)
    count, busy = 0, False
    active = {'running': 0, 'queued': 0}
    active_rows = []
    pilot = None
    for row in rows:
        if row.get('status') in active:
            active[row['status']] += 1
            busy = True
            if fresh:
                active_rows.append(row)
                continue
        if (fresh and pilot is None and row.get('host') == 'LAB-08B'
                and not row.get('kind') and row.get('status') == 'succeeded'
                and row.get('phase') == 'maintenance'):
            pilot = row
        if start <= count < start + PAGE_SIZE:
            selected.append(row)
        tail.append(row)
        count += 1
    pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
    current = min(number, pages)
    if current != number:
        length = count - (current - 1) * PAGE_SIZE
        selected = list(tail)[-length:] if length else []
    result = dict(data)
    result[key] = selected
    result['pagination'] = dict(page=current, pages=pages, total=count, page_size=PAGE_SIZE)
    result['busy'] = bool(data['busy']) if 'busy' in data and not fresh else bool(data.get('busy')) or busy
    if fresh:
        result.update(active_counts=active, pilot_run=pilot,
                      active_runs=sorted(active_rows,key=lambda row:(
                          row['status']=='queued',row.get('started',''),row.get('id',''))))
    return result
