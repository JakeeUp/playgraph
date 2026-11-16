"""Only normalized raster pixels leave the upload boundary."""
from io import BytesIO
import warnings

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000
SIZES = {"avatar": (256, 256), "cover": (1440, 480)}


def normalize_image(data: bytes, kind: str) -> bytes:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Choose an image under 2 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as source:
                if source.width * source.height > MAX_PIXELS or getattr(source, "n_frames", 1) != 1:
                    raise HTTPException(422, "Choose a still image under 16 million pixels.")
                source.load()
                image = ImageOps.fit(ImageOps.exif_transpose(source).convert("RGB"), SIZES[kind],
                                     method=Image.Resampling.LANCZOS)
                # A fresh image discards EXIF, GPS, comments and embedded profiles.
                clean = Image.new("RGB", image.size)
                clean.paste(image)
                output = BytesIO()
                clean.save(output, format="WEBP", quality=82)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning,
            Image.DecompressionBombError):
        raise HTTPException(422, "Choose a valid JPEG, PNG or WebP image.") from None
