"""
CSV Loader — reads CSV files and auto-maps columns to a canonical schema
using fuzzy header matching against the schema registry.
"""

from __future__ import annotations
import csv
import io
import json
import os
from pathlib import Path

from rapidfuzz import fuzz

from app.models.entities import RawRecord

SCHEMA_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "schema_registry.json"


def _load_schema_registry() -> dict:
    with open(SCHEMA_REGISTRY_PATH) as f:
        return json.load(f)


def _best_source_match(headers: list[str], registry: dict) -> tuple[str, dict[str, str]]:
    """
    Given a CSV's header row, determine which source type it most likely is
    (hr, payroll, licenses) and return a mapping of {canonical_field: actual_header}.
    """
    best_source = ""
    best_score = 0
    best_mapping: dict[str, str] = {}

    for source, schema in registry.items():
        mapping: dict[str, str] = {}
        total_score = 0

        for canonical, aliases in schema["canonical_fields"].items():
            top_match = ""
            top_ratio = 0
            for header in headers:
                for alias in aliases:
                    ratio = fuzz.ratio(header.lower().strip(), alias.lower())
                    if ratio > top_ratio:
                        top_ratio = ratio
                        top_match = header
            if top_ratio >= 60:  # threshold for accepting a fuzzy match
                mapping[canonical] = top_match
                total_score += top_ratio

        avg = total_score / max(len(schema["canonical_fields"]), 1)
        if avg > best_score:
            best_score = avg
            best_source = source
            best_mapping = mapping

    return best_source, best_mapping


def load_csv(file_content: str | bytes, filename: str = "unknown.csv") -> tuple[str, list[RawRecord]]:
    """
    Parse CSV content, auto-detect source type, and return a list of RawRecords.
    Returns (detected_source_type, list_of_raw_records).
    """
    if isinstance(file_content, bytes):
        file_content = file_content.decode("utf-8", errors="replace")

    registry = _load_schema_registry()
    reader = csv.DictReader(io.StringIO(file_content))
    headers = reader.fieldnames or []

    source_type, column_map = _best_source_match(headers, registry)

    records: list[RawRecord] = []
    for row in reader:
        # Store both raw data and the mapped canonical data
        raw_data = dict(row)
        mapped_data = {}
        for canonical, actual_col in column_map.items():
            mapped_data[canonical] = row.get(actual_col, "").strip()

        record = RawRecord(
            source=source_type,
            source_file=filename,
            data={
                "raw": raw_data,
                "mapped": mapped_data,
                "column_mapping": column_map,
            },
        )
        records.append(record)

    return source_type, records
