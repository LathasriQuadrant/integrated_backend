"""
Parser for Tableau .twbx packaged workbooks.

A .twbx is a zip archive containing exactly one .twb file (plus data
extracts, images, etc.). This module extracts the embedded .twb bytes
in memory and hands them to TwbParser -- nothing is written to disk.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from app.services.discovery.twb_parser import TwbParser


class TwbxParser:
    def __init__(self, twbx_bytes: bytes):
        self._twbx_bytes = twbx_bytes

    def _extract_twb_bytes(self) -> bytes:
        with zipfile.ZipFile(io.BytesIO(self._twbx_bytes)) as archive:
            twb_names = [n for n in archive.namelist() if n.lower().endswith(".twb")]
            if not twb_names:
                raise ValueError("No .twb file found inside the .twbx package.")
            with archive.open(twb_names[0]) as twb_file:
                return twb_file.read()

    def parse_all(self) -> dict[str, Any]:
        twb_bytes = self._extract_twb_bytes()
        return TwbParser(twb_bytes).parse_all()


def parse_workbook_file(filename: str, file_bytes: bytes) -> dict[str, Any]:
    """Dispatch to the right parser based on file extension."""

    lower = filename.lower()
    if lower.endswith(".twbx"):
        return TwbxParser(file_bytes).parse_all()
    if lower.endswith(".twb"):
        return TwbParser(file_bytes).parse_all()
    raise ValueError(f"Unsupported workbook file type: {filename}")
