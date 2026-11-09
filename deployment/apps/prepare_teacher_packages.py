"""Render copyable plain source for the existing Teacher's package extension."""
import difflib
import hashlib
from pathlib import Path
import runpy

ROOT=Path(__file__).resolve().parents[2]


def render(previous,root=ROOT):
    old_installer=runpy.run_path(str(previous/'deployment/teacher/install.py'))
    new_installer=runpy.run_path(str(root/'deployment/teacher/install.py'))
    empty=dict(receiver='',preview='',software_client='')
    # PUBLIC is deliberately a dummy: the wrapper contains no key material.
    wrapper=Path('/usr/local/sbin/cisco-lab-teacher-ssh')
    old_wrapper=old_installer['files']('ssh-ed25519 PUBLIC',empty)[wrapper][0]
    new_wrapper=new_installer['files']('ssh-ed25519 PUBLIC',dict(empty,deb_client=''))[wrapper][0]
    library=Path('/usr/local/lib/cisco-lab-teacher')
    pairs=[(library/'receiver.py',(previous/'deployment/teacher/receiver.py').read_bytes(),(root/'deployment/teacher/receiver.py').read_bytes(),0o644),
           (library/'software_client.py',(previous/'dashboard/software_client.py').read_bytes(),(root/'dashboard/software_client.py').read_bytes(),0o644),
           (library/'deb_client.py',None,(root/'dashboard/deb_client.py').read_bytes(),0o644),
           (wrapper,old_wrapper,new_wrapper,0o755)]
    rows=[]
    for path,before,after,mode in pairs:
        a=before.decode().splitlines(keepends=True) if before else [];b=after.decode().splitlines(keepends=True)
        edits=[]
        for action,first,last,start,end in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
            if action!='equal':edits.append((first,last,''.join(b[start:end])))
        rows.append(dict(path=str(path),old=hashlib.sha256(before).hexdigest() if before else None,
                         new=hashlib.sha256(after).hexdigest(),mode=mode,edits=edits))
    literal='PATCHES = [\n'
    for row in rows:
        literal+='    '+repr({key:value for key,value in row.items() if key!='edits'})[:-1]+", 'edits': [\n"
        for first,last,text in row['edits']:
            content="r'''"+text+"'''" if "'''" not in text and not text.endswith('\\') else repr(text)
            literal+='        ('+str(first)+','+str(last)+','+content+'),\n'
        literal+='    ]},\n'
    literal+=']'
    source=(root/'deployment/teacher/package_update.py').read_text().replace('PATCHES = None',literal,1)
    compile(source,'<teacher-package-update>','exec')
    return source,rows,pairs
