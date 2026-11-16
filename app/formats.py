IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")
VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
TMP_PREFIX = ".metaedit-tmp-"


def kind_of(ext: str):
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None
