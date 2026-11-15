"""Downloads-as-a-Diary.

Turn a downloads folder into a dated, visual diary.

One indexing pass produces two things and nothing else:

    previews/     many preview assets (thumbnails, page renders, video frames)
    index.json    ONE manifest mapping which previews belong to what, and when

Everything downstream is a thin viewer that reads the manifest: a static HTML
timeline, an Obsidian vault with a Base. Viewers own no truth and can be
regenerated or deleted at will.

The input folder is never modified.
"""

__version__ = "0.1.0"
SCHEMA_VERSION = 2
