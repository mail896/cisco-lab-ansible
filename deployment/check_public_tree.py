"""Validate the actual Git index or committed public tree before publication."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

try:
    from .prepare_public_export import validate_files
    from .public_profile import load_profile, validate_profile
except ImportError:
    from prepare_public_export import validate_files
    from public_profile import load_profile, validate_profile


def check(root, *, staged=False, ref='HEAD', profile_root=None):
    root=Path(root).resolve()
    git=['git','-C',str(root)]
    args=['ls-files','--stage','-z'] if staged else ['ls-tree','-r','-z',ref]
    rows=subprocess.check_output(git+args).split(b'\0')
    objects=[]
    for row in filter(None,rows):
        meta,name=row.split(b'\t',1);fields=meta.split()
        if fields[0] not in (b'100644',b'100755'):
            raise ValueError('Nonregular public Git entry: '+name.decode())
        if staged and fields[2]!=b'0':raise ValueError('Unresolved index entry.')
        objects.append((fields[1] if staged else fields[2],name.decode()))
    process=subprocess.Popen(git+['cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    files={}
    try:
        for oid,name in objects:
            process.stdin.write(oid+b'\n');process.stdin.flush()
            header=process.stdout.readline().split()
            if len(header)!=3 or header[1]!=b'blob':raise ValueError('Missing public Git blob: '+name)
            files[name]=process.stdout.read(int(header[2]));process.stdout.read(1)
    finally:
        process.stdin.close();process.stdout.close();process.wait()
    validate_files(files)
    validate_profile(files,load_profile(profile_root or root))
    if profile_root:
        reviews=Path(profile_root)/'local-private/publication-images.json'
        approved=json.loads(reviews.read_text()) if reviews.exists() else {}
        for name,body in files.items():
            if Path(name).suffix.lower() in {'.jpg','.png'} and approved.get(name)!=hashlib.sha256(body).hexdigest():
                raise ValueError('Public image requires renewed privacy review: '+name)
    print('Public Git '+('index' if staged else ref)+': '+str(len(files))+' files passed path, secret, infrastructure and link checks.')
    return files


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--profile-root',type=Path)
    parser.add_argument('--staged',action='store_true')
    parser.add_argument('--ref',default='HEAD')
    args=parser.parse_args()
    check(args.root,staged=args.staged,ref=args.ref,profile_root=args.profile_root)
