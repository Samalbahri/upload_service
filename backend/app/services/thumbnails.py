import os
import tempfile
import warnings
from pathlib import Path

from PIL import Image, ImageOps


THUMBNAIL_DIR = Path("storage/thumbnails")
THUMBNAIL_SIZE = (200, 200)
MAX_IMAGE_PIXELS = 20_000_000


def thumbnail_path(sha256: str) -> Path:
    # The hash should come from the stored File record.
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise ValueError("Invalid SHA-256 hash")

    return THUMBNAIL_DIR / f"{sha256}-200x200-v1.webp"


def generate_thumbnail(original_path: Path, sha256: str, *, output_path: Path | None = None) -> Path:
    destination = output_path if output_path is not None else thumbnail_path(sha256)
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp_path = None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)

            with Image.open(original_path) as image:
                # Check dimensions before decoding the full image.
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Image exceeds decoded pixel limit")

                # Correct camera orientation before resizing.
                with ImageOps.exif_transpose(image) as oriented:
                    oriented.thumbnail(THUMBNAIL_SIZE)

                    # Normalize the mode for WebP output, preserving transparency.
                    with oriented.convert("RGBA") as output:
                        with tempfile.NamedTemporaryFile(
                            dir=destination.parent,
                            prefix=".thumbnail-",
                            suffix=".webp",
                            delete=False,
                        ) as temp:
                            temp_path = Path(temp.name)
                            output.save(
                                temp,
                                format="WEBP",
                                quality=80,
                            )

        # Publish only after the complete output file has been closed.
        os.replace(temp_path, destination)
        return destination

    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)