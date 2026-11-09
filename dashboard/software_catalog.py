"""Build a small searchable catalogue from Debian's existing APT metadata.

Run with Debian's /usr/bin/python3 so its existing python3-apt is available.
Never updates APT lists, installs packages, or writes outside the output file.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import socket
import sys


def entries():
    import apt_pkg
    apt_pkg.init_config()
    apt_pkg.config.set('Dir::Cache::pkgcache', '')
    apt_pkg.config.set('Dir::Cache::srcpkgcache', '')
    apt_pkg.init_system()
    cache = apt_pkg.Cache(None)
    policy = apt_pkg.DepCache(cache)
    records = apt_pkg.PackageRecords(cache)
    for package in cache.packages:
        if package.architecture not in ('amd64', 'all'):
            continue
        version = policy.get_candidate_ver(package)
        if not version:
            continue
        files = [(source, offset) for source, offset in version.file_list
                 if source.origin in ('Debian', 'Debian-Security')
                 and (source.codename or '').split('-')[0] == 'trixie']
        if not files:
            continue
        records.lookup(files[0])
        fields = apt_pkg.TagSection(records.record)
        if ':' not in package.name:
            yield [package.name,version.ver_str,fields.get('Description','').strip()[:4000],
                   fields.get('Section',''),int(fields.get('Size','0')),files[0][0].component]
    dates = [path.stat().st_mtime for path in Path('/var/lib/apt/lists').glob('*InRelease')
             if 'debian' in path.name]
    yield dict(release='Debian 13 · trixie',architecture='amd64',source_host=socket.gethostname(),
               lists_at=datetime.fromtimestamp(min(dates),timezone.utc).isoformat() if dates else None)


def build(destination, rows):
    db = sqlite3.connect(destination)
    try:
        db.executescript("""
            CREATE TABLE packages(name TEXT PRIMARY KEY, version TEXT, description TEXT,
                                  section TEXT, bytes INTEGER, origin TEXT);
            CREATE VIRTUAL TABLE search USING fts5(name, description, tokenize='unicode61');
            CREATE TABLE meta(value TEXT);
        """)
        count = 0
        metadata = {}
        for row in rows:
            if isinstance(row,dict):
                metadata = row
                continue
            if not isinstance(row,list) or len(row)!=6:
                raise ValueError('Ungültige Paketmetadaten.')
            db.execute('INSERT OR REPLACE INTO packages VALUES(?,?,?,?,?,?)',row)
            count += 1
        db.execute('INSERT INTO search(name,description) SELECT name,description FROM packages')
        count = db.execute('SELECT COUNT(*) FROM packages').fetchone()[0]
        meta = dict(metadata,count=count,indexed_at=datetime.now(timezone.utc).isoformat())
        db.execute('INSERT INTO meta VALUES(?)', (json.dumps(meta),))
        db.commit()
        os.chmod(destination, 0o600)
    finally:
        db.close()
    if count < 1000:
        raise ValueError('Debian-Paketlisten fehlen oder sind unvollständig.')


if __name__ == '__main__':
    if sys.argv[1]=='--export':
        for entry in entries():
            print(json.dumps(entry,ensure_ascii=False))
    elif sys.argv[1]=='--import':
        build(sys.argv[2],(json.loads(line) for line in sys.stdin))
    else:
        build(sys.argv[1],entries())
