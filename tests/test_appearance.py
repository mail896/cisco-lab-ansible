"""Exercise actual bounded image conversion without changing the active LAB images."""
import json
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from dashboard.appearance import Appearance


class AppearanceTests(unittest.TestCase):
    def test_upload_normalizes_metadata_and_never_replaces_existing_images_or_selection(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);images=root/'images';images.mkdir()
            buffer=io.BytesIO();exif=Image.Exif();exif[270]='private comment'
            Image.new('RGB',(160,90),'orange').save(buffer,format='JPEG',exif=exif)
            with patch('dashboard.appearance.FOLDER',images):
                manager=Appearance(root/'state');manager.select('','')
                selected=manager.settings.read_bytes()
                first=manager.upload('Schulnetz ä.jpg',buffer.getvalue())
                self.assertEqual(first['uploaded'],'Schulnetz ä.jpg')
                original=(images/first['uploaded']).read_bytes()
                second=manager.upload('Schulnetz ä.jpg',buffer.getvalue())
                self.assertEqual(second['uploaded'],'Schulnetz ä-2.jpg')
                self.assertEqual((images/first['uploaded']).read_bytes(),original)
                self.assertEqual(manager.settings.read_bytes(),selected)
                self.assertEqual((images/second['uploaded']).stat().st_mode&0o777,0o600)
                with Image.open(images/first['uploaded']) as image:
                    self.assertFalse(image.getexif())
                self.assertIn(first['uploaded'],second['choices']['images'])

    def test_upload_rejects_path_traversal_invalid_and_oversized_images(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);images=root/'images';images.mkdir()
            buffer=io.BytesIO();Image.new('RGB',(20,10),'red').save(buffer,format='PNG')
            with patch('dashboard.appearance.FOLDER',images):
                manager=Appearance(root/'state')
                for name in ('../outside.png','sub/a.png','sub\\a.png','.hidden.png','x.svg','x\n.png','ä'*100+'.png'):
                    with self.assertRaises(ValueError):manager.upload(name,buffer.getvalue())
                for data in (b'',b'not an image',b'\x89PNG\r\n\x1a\ninvalid'):
                    with self.assertRaises(ValueError):manager.upload('broken.png',data)
                with patch('dashboard.appearance.MAX_FILE',10):
                    with self.assertRaises(ValueError):manager.upload('large.png',buffer.getvalue())
                self.assertEqual(list(images.iterdir()),[])
                self.assertFalse(manager.settings.exists())

    def test_previews_are_small_metadata_free_cached_and_do_not_change_selection(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = root/'images'; images.mkdir()
            exif = Image.Exif(); exif[270] = 'private comment'
            Image.new('RGB', (1920, 1080), 'green').save(images/'custom.jpg', exif=exif)
            with patch('dashboard.appearance.FOLDER', images):
                manager = Appearance(root/'state')
                manager.select('custom.jpg', '')
                original = manager.settings.read_bytes()
                choices = manager.choices()
                token = choices['previews']['desktop']['active']
                preview = manager.preview(token)
                with Image.open(io.BytesIO(preview)) as image:
                    self.assertEqual(image.format, 'JPEG')
                    self.assertEqual(image.size, (720, 405))
                    self.assertFalse(image.getexif())
                self.assertLess(len(preview), 1024 * 1024)
                with patch('dashboard.appearance.subprocess.run', side_effect=AssertionError('cache miss')):
                    self.assertEqual(manager.preview(token), preview)
                self.assertEqual(manager.settings.read_bytes(), original)
                self.assertFalse(list(manager.directory.glob('.preview-*')))

    def test_preview_rejects_unknown_changed_symlink_and_invalid_image(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = root/'images'; images.mkdir()
            picture = images/'custom.png'
            Image.new('RGB', (64, 36), 'blue').save(picture)
            (images/'link.png').symlink_to(picture)
            (images/'broken.png').write_bytes(b'\x89PNG\r\n\x1a\ninvalid')
            with patch('dashboard.appearance.FOLDER', images):
                manager = Appearance(root/'state')
                choices = manager.choices()
                self.assertNotIn('link.png', choices['images'])
                for token in ('../custom.png', 'a'*64):
                    with self.assertRaises(FileNotFoundError): manager.preview(token)
                token = choices['image_previews']['custom.png']
                manager.preview(token)
                Image.new('RGB', (65, 36), 'red').save(picture)
                with self.assertRaises(FileNotFoundError): manager.preview(token)
                token = manager.choices()['image_previews']['custom.png']
                picture.unlink(); picture.symlink_to(images/'broken.png')
                with self.assertRaises(FileNotFoundError): manager.preview(token)
                with self.assertRaises(ValueError): manager.preview(choices['image_previews']['broken.png'])
                self.assertFalse(manager.settings.exists())

    def test_png_and_exif_jpeg_are_normalized_without_metadata(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = root / 'images'; images.mkdir()
            Image.new('RGBA', (160, 90), (255, 0, 0, 0)).save(images / 'transparent.png')
            exif = Image.Exif(); exif[274] = 6; exif[270] = 'private comment'
            Image.new('RGB', (80, 40), 'green').save(images / 'rotated.jpg', exif=exif)
            with patch('dashboard.appearance.FOLDER', images):
                manager = Appearance(root / 'state')
                manager.select('transparent.png', 'rotated.jpg')
                active = manager.active()
                with Image.open(active['desktop'][1]) as image:
                    self.assertEqual(image.mode, 'RGB')
                    self.assertEqual(image.getpixel((0, 0)), (23, 38, 58))
                with Image.open(active['lock'][1]) as image:
                    self.assertEqual(image.size, (40, 80))
                    self.assertFalse(image.getexif())

    def test_invalid_or_excessive_image_preserves_previous_selection(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            images = root / 'images'; images.mkdir()
            (images / 'broken.png').write_bytes(b'\x89PNG\r\n\x1a\ninvalid')
            Image.new('1', (6000, 5000)).save(images / 'excessive.png')
            with patch('dashboard.appearance.FOLDER', images):
                manager = Appearance(root / 'state')
                manager.select('', '')
                original = manager.settings.read_bytes()
                for name in ('broken.png', 'excessive.png'):
                    with self.assertRaises(ValueError):
                        manager.select(name, '')
                    self.assertEqual(manager.settings.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
