"""File-kind classification by extension.

A `kind` decides which extractor runs and how a viewer badges the item. Keep
this the single place where extensions are mapped, so adding a format is a
one-line change.
"""

from __future__ import annotations

# Order matters only for readability; extensions are unique across groups.
_KIND_EXTS: dict[str, set[str]] = {
    "image": {
        "jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "tiff", "tif",
        "heic", "heif", "ico", "icns", "svg", "jp2", "jfif",
    },
    "video": {
        "mp4", "mov", "mkv", "avi", "webm", "m4v", "mpg", "mpeg", "wmv",
        "flv", "3gp", "mts", "m2ts",
    },
    "audio": {
        "mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "aiff", "aif",
        "wma", "m4b",
    },
    "pdf": {"pdf"},
    "document": {
        "docx", "doc", "pptx", "ppt", "xlsx", "xls", "odt", "ods", "odp",
        "rtf", "epub", "pages", "numbers", "key",
    },
    "archive": {"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar", "jar"},
    "text": {
        "txt", "md", "markdown", "rst", "log", "csv", "tsv", "json", "jsonl",
        "yaml", "yml", "toml", "ini", "cfg", "conf", "xml", "html", "htm",
        "srt", "vtt", "tex", "bib",
    },
    "code": {
        "py", "js", "mjs", "cjs", "ts", "tsx", "jsx", "c", "cc", "cpp", "h",
        "hpp", "java", "go", "rs", "rb", "php", "sh", "bash", "zsh", "fish",
        "sql", "swift", "kt", "kts", "scala", "lua", "pl", "r", "vue", "svelte",
        "css", "scss", "less", "wasm", "asm", "patch", "diff",
    },
}

EXT_TO_KIND: dict[str, str] = {
    ext: kind for kind, exts in _KIND_EXTS.items() for ext in exts
}

ALL_KINDS = tuple(_KIND_EXTS) + ("other",)

# Office formats that are really ZIP containers of XML — handled by the
# `office` extractor without any third-party library.
OOXML_EXTS = {"docx", "pptx", "xlsx"}

# Extensions Pillow cannot open; routed to PyMuPDF instead.
VECTOR_IMAGE_EXTS = {"svg"}

_MIME_BY_EXT: dict[str, str] = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "avif": "image/avif",
    "svg": "image/svg+xml", "heic": "image/heic", "tiff": "image/tiff",
    "pdf": "application/pdf", "zip": "application/zip",
    "mp4": "video/mp4", "mov": "video/quicktime", "mkv": "video/x-matroska",
    "avi": "video/x-msvideo", "webm": "video/webm",
    "mp3": "audio/mpeg", "m4a": "audio/mp4", "flac": "audio/flac",
    "wav": "audio/wav", "ogg": "audio/ogg",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "html": "text/html", "htm": "text/html", "json": "application/json",
    "csv": "text/csv", "md": "text/markdown", "txt": "text/plain",
    "py": "text/x-python",
}


def kind_for_ext(ext: str) -> str:
    """Map a bare extension (no dot, any case) to a kind."""
    return EXT_TO_KIND.get(ext.lower(), "other")


def mime_for_ext(ext: str) -> str:
    """Best-effort MIME type. Falls back to the stdlib, then to octet-stream."""
    ext = ext.lower()
    if ext in _MIME_BY_EXT:
        return _MIME_BY_EXT[ext]
    import mimetypes

    guessed, _ = mimetypes.guess_type(f"x.{ext}")
    return guessed or "application/octet-stream"
