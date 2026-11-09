"""Real tiny .deb archives and simulated APT actions; never installs a package."""
from contextlib import nullcontext
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import zipfile

from dashboard import deb_client,software
from dashboard.own_packages import OwnPackages


def deb(root,version='1.0',architecture='all'):
    folder=root/('build-'+version+'-'+architecture);(folder/'DEBIAN').mkdir(parents=True)
    (folder/'DEBIAN/control').write_text('Package: lab-example\nVersion: '+version+'\nArchitecture: '+architecture+
        '\nMaintainer: LAB <lab@example.invalid>\nDescription: A small LAB example\n')
    path=root/(folder.name+'.deb')
    subprocess.run(['dpkg-deb','--build','--root-owner-group',str(folder),str(path)],check=True,capture_output=True)
    return path


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.library=OwnPackages(self.root/'library');self.package=deb(self.root)

    def upload(self,path=None):
        path=path or self.package
        with path.open('rb') as stream:return self.library.upload(stream,path.stat().st_size,path.name)

    def test_real_deb_upload_metadata_content_address_and_duplicate_are_consistent(self):
        row=self.upload();again=self.upload()
        self.assertEqual(row,again);self.assertEqual(row['name'],'lab-example');self.assertEqual(row['version'],'1.0')
        self.assertEqual(self.library.path(self.library.snapshot(['lab-example'])[0]).read_bytes(),self.package.read_bytes())
        self.assertEqual(len(list(self.library.directory.glob('*.deb'))),1)
        self.assertEqual(self.library.rows(),[row])
        self.assertEqual(self.library.path(self.library.snapshot(['lab-example'])[0]).stat().st_mode&0o777,0o600)

    def test_wrong_architecture_truncated_archive_and_path_names_are_rejected(self):
        with self.assertRaises(ValueError):self.upload(deb(self.root,'1.0','arm64'))
        for name,body,size in [('../outside.deb',b'archive',7),('example.deb',b'bad',3),('example.deb',b'',8)]:
            with self.subTest(name=name),self.assertRaises(ValueError):self.library.upload(io.BytesIO(body),size,name)
        self.assertFalse(self.library.rows());self.assertFalse(list(self.library.directory.glob('.upload-*')))

    def test_receive_requires_exact_size_and_digest_and_removes_partial_files(self):
        body=self.package.read_bytes();digest=hashlib.sha256(body).hexdigest();cache=self.root/'client-cache'
        original=Path.lstat
        def root_stat(path):
            info=original(path)
            if path==cache or cache in path.parents:return SimpleNamespace(st_mode=info.st_mode,st_uid=0,st_size=info.st_size)
            return info
        with patch.object(deb_client,'CACHE',cache),patch.object(deb_client,'require_client'),patch.object(Path,'lstat',root_stat):
            reply=deb_client.receive(io.BytesIO(body),'LAB-08A',digest,len(body))
            self.assertEqual(reply,dict(received=digest,size=len(body)))
            row=self.upload();manifest={key:row[key] for key in deb_client.FIELDS}
            self.assertEqual(deb_client.cached([manifest],'LAB-08A'),dict(cached=[digest]))
            for stream,sha,size in [(io.BytesIO(body[:-1]),digest,len(body)),(io.BytesIO(body), 'a'*64,len(body)),
                                    (io.BytesIO(body+b'extra'),digest,len(body))]:
                with self.assertRaises(ValueError):deb_client.receive(stream,'LAB-08A',sha,size)
            self.assertFalse(list(cache.glob('.receive-*')))

    def test_profile_approval_tracks_own_version_and_export_contains_actual_deb(self):
        manager=software.Software(self.root/'software',SimpleNamespace(lock=threading.Lock()))
        self.library=manager.own;row=self.upload()
        profile=manager.save_profile('','Eigene Tools',['lab-example'],False)
        approved=software.package_revision(profile['packages'],self.library.snapshot(profile['packages']))
        manager.atomic(manager.directory/'profiles'/(profile['id']+'.json'),dict(profile,approved=approved,baseline=True))
        self.assertEqual(manager.profile(profile['id'])['approved'],approved)
        self.assertEqual(software.baseline_packages(manager.directory/'profiles'),['lab-example'])
        output=io.BytesIO();manager.write_export_zip(profile['id'],output)
        with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
            self.assertEqual(archive.read('packages/'+row['sha256']+'.deb'),self.package.read_bytes())
            self.assertIn(b'lab_software_debs',archive.read('softwareprofil.yml'))
        self.upload(deb(self.root,'2.0'))
        self.assertIsNone(manager.profile(profile['id'])['approved'])
        with self.assertRaisesRegex(ValueError,'Pilotabnahme'):software.baseline_packages(manager.directory/'profiles')

    def test_own_plan_uses_real_archive_metadata_and_exact_local_file_not_remote_package_name(self):
        row=self.upload();manifest={key:row[key] for key in deb_client.FIELDS};cache=self.root/'client-cache';cache.mkdir()
        shutil.copyfile(self.package,cache/(row['sha256']+'.deb'))
        candidate=SimpleNamespace(size=4096)
        apt=SimpleNamespace(Cache=lambda:{'lib-example':SimpleNamespace(candidate=candidate)})
        real_run=subprocess.run
        def run(args,**kwargs):
            if args[0]=='apt-get':
                self.assertIn(str(cache/(row['sha256']+'.deb')),args)
                return subprocess.CompletedProcess(args,0,'Inst lab-example (1.0 local-deb [all])\nInst lib-example (2.0 Debian [amd64])\n','')
            return real_run(args,**kwargs)
        with patch.dict(sys.modules,apt=apt),patch.object(deb_client,'require_client'),patch.object(deb_client,'CACHE',cache), \
                patch.object(deb_client,'cached',return_value=dict(cached=[row['sha256']])),patch.object(deb_client.subprocess,'run',side_effect=run):
            plan=deb_client.plan(['lab-example'],'LAB-08A',[manifest])
        self.assertEqual(plan['removals'],[]);self.assertEqual(plan['download_bytes'],4096)
        self.assertEqual(len(plan['changes']),2)
        with patch.object(deb_client,'CACHE',cache):
            self.assertEqual(deb_client.install_arguments(plan,['lab-example'],[manifest]),[str(cache/(row['sha256']+'.deb'))])

    def test_same_package_version_with_changed_content_requires_new_version(self):
        row=self.upload();folder=self.root/'altered';folder.mkdir()
        path=folder/'changed.deb'
        unpack=self.root/'build-1.0-all';(unpack/'extra.txt').write_text('changed')
        subprocess.run(['dpkg-deb','--build','--root-owner-group',str(unpack),str(path)],check=True,capture_output=True)
        with self.assertRaisesRegex(ValueError,'Paketversion erhöhen'):self.upload(path)
        self.assertEqual(self.library.rows()[0]['sha256'],row['sha256'])
