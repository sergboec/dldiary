"""Viewers: thin, regenerable consumers of `index.json`.

A viewer reads the manifest and writes a presentation of it. It holds no
state of its own and is never the source of truth — delete one, re-render,
and nothing is lost.
"""
