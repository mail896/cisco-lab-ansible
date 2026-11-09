"""Decode only PNG/JPEG in a separate process with bounded resources."""
from pathlib import Path
import resource
import sys
import warnings


def main():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (48 * 1024 * 1024,) * 2)
    from PIL import Image, ImageOps

    source, destination, decoder, output_format = sys.argv[1:]
    if decoder not in ('png', 'jpeg') or output_format not in ('png', 'jpeg', 'preview'):
        raise ValueError('Unsupported image format')
    if not 0 < Path(source).stat().st_size <= 48 * 1024 * 1024:
        raise ValueError('Image input is too large')
    Image.MAX_IMAGE_PIXELS = 24_000_000
    warnings.simplefilter('error', Image.DecompressionBombWarning)
    with Image.open(source, formats=[decoder.upper()]) as opened:
        if max(opened.size) > 10000:
            raise ValueError('Image dimensions are too large')
        bounds = (720, 405) if output_format == 'preview' else (3840, 2160)
        opened.draft('RGB', bounds)
        image = ImageOps.exif_transpose(opened)
        image.thumbnail(bounds, Image.Resampling.LANCZOS)
        rgba = image.convert('RGBA')
        # A fresh canvas drops EXIF, comments, profiles and other input metadata.
        clean = Image.new('RGB', rgba.size, '#17263a')
        clean.paste(rgba, mask=rgba.getchannel('A'))
        clean.save(destination, format='JPEG' if output_format == 'preview' else output_format.upper(),
                   quality=82 if output_format == 'preview' else 92)


if __name__ == '__main__':
    main()
