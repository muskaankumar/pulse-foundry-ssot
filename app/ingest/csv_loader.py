from __future__ import annotations
import csv
import io
import json
import os
from pathlib import Path
from rapidfuzz import fuzz

from app.models.entities import RawRecord
from app.ingest.pdf_loader import _normalize_day, _parse_shift_hours, _load_aliases

SCHEMA_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "config" / "schema_registry.json"

def _load_schema_registry() -> dict:
    with open(SCHEMA_REGISTRY_PATH) as f:
        return json.load(f)

def _best_source_match(headers: list[str], registry: dict) -> tuple[str, dict[str, str]]:
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
            if top_ratio >= 60:
                mapping[canonical] = top_match
                total_score += top_ratio

        avg = total_score / max(len(schema["canonical_fields"]), 1)
        if avg > best_score:
            best_score = avg
            best_source = source
            best_mapping = mapping

    return best_source, best_mapping

def load_csv(file_content: str | bytes, filename: str = "unknown.csv") -> tuple[str, list[RawRecord]]:
    if isinstance(file_content, bytes):
        file_content = file_content.decode("utf-8", errors="replace")

    registry = _load_schema_registry()
    reader = csv.DictReader(io.StringIO(file_content))
    headers = reader.fieldnames or []

    # 1. Detect wide schedule format by looking for day columns
    day_columns = {}
    for h in headers:
        day, raw_date = _normalize_day(h)
        if day:
            day_columns[h] = (day, raw_date)
            
    is_wide_schedule = len(day_columns) >= 3

    # 2. Unpivot wide format into daily shift records
    if is_wide_schedule:
        source_type = "schedule"
        aliases = _load_aliases()
        records: list[RawRecord] = []
        
        name_col = next((h for h in headers if h.lower() in ("employee", "name", "staff", "employee_name")), None)
        fac_col = next((h for h in headers if h.lower() in ("facility", "location", "site")), None)
        role_col = next((h for h in headers if h.lower() in ("role", "title", "job")), None)

        for row in reader:
            raw_name = row.get(name_col, "").strip() if name_col else ""
            if not raw_name:
                continue
                
            facility = row.get(fac_col, "").strip() if fac_col else "Unknown"
            role = row.get(role_col, "").strip() if role_col else ""

            for day_header, (day_name, raw_date) in day_columns.items():
                shift = row.get(day_header, "").strip()
                if not shift:
                    shift = "OFF"
                hours = _parse_shift_hours(shift, aliases)

                record = RawRecord(
                    source="schedule",
                    source_file=filename,
                    data={
                        "raw": dict(row),
                        "mapped": {
                            "employee_name": raw_name,
                            "role": role,
                            "facility": facility,
                            "day": day_name,
                            "date": raw_date or "",
                            "shift": shift,
                            "hours": hours,
                            "period_start": "2026-09-14",  # Fallback for demo
                            "period_end": "2026-09-20",
                        },
                    },
                )
                records.append(record)
        return source_type, records

    # 3. Standard parsing for HR, Payroll, and Licenses
    source_type, column_map = _best_source_match(headers, registry)
    records: list[RawRecord] = []
    
    for row in reader:
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