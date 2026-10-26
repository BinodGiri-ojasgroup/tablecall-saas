"""Image uploads: validate, strip metadata, resize, re-encode as WebP."""
from __future__ import annotations

from io import BytesIO

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 40_000_000
ALLOWED_FORMATS = ["JPEG", "PNG", "WEBP", "GIF"]
SIZES = {"item": 900, "logo": 480}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def process_image(raw: bytes, kind: str) -> bytes:
    """Return a re-encoded, size-limited WebP. Re-encoding drops EXIF/GPS data and any non-image payload."""
    try:
        img = Image.open(BytesIO(raw), formats=ALLOWED_FORMATS)
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        raise HTTPException(status_code=400, detail="That file isn't a usable image. Use a JPG, PNG, WebP or GIF.") from None
    img = ImageOps.exif_transpose(img)
    limit = SIZES.get(kind, 900)
    img.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "transparency" in img.info or img.mode in ("LA", "PA") else "RGB")
    out = BytesIO()
    img.save(out, format="WEBP", quality=82, method=4)
    return out.getvalue()
