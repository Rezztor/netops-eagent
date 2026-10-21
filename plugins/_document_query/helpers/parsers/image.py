"""Image parser: Tesseract, which the image carries."""
from plugins._document_query.helpers.fetch import FetchedDocument
from .base import BaseParser


class ImageParser(BaseParser):
    mimetypes = ["image/"]

    def _parse_sync(self, document: FetchedDocument, config: dict) -> str:
        # Not UnstructuredLoader, which this handed the image to until 2026-10-13: its
        # reading of an image starts by fetching a layout model from the Hugging Face
        # Hub, so on a network without the internet no image was read at all.
        import pytesseract
        from PIL import Image

        with document.local_file() as file_path, Image.open(file_path) as image:
            return pytesseract.image_to_string(image)
