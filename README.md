# Pulse Foundry — Single Source of Truth

**Unified data reconciliation engine** that ingests messy data from multiple disconnected systems, resolves entities across sources, detects conflicts, and surfaces actionable intelligence — built for Harborview Care Group's skilled nursing facilities, but architectured to work for any industry.

---

## The Problem

Harborview Care Group runs two facilities (Bayside and Riverdale) with ~10 employees. Their data lives in four separate systems — HR, Payroll, Licensing, and Staff Scheduling — that don't agree with each other. The same person appears differently across systems (name casing, abbreviations, typos), leading to:

1. **Slow patient referrals** — can't quickly answer "do we have capacity?"
2. **Painful state reporting** — quarterly reports take weeks and aren't trusted
3. **Expiring credentials** — licenses expire without notice

## The Solution

A three-layer pipeline that creates a **single source of truth** from raw, messy data.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Layer 1: Ingestion & Normalization Engine       │
│  CSV/PDF → auto-detect schema → normalize →      │
│  standardize names, facilities, job codes         │
├─────────────────────────────────────────────────┤
│  Layer 2: Entity Resolution & Reconciliation     │
│  Fuzzy match across sources → build unified      │
│  Person/Facility entities → detect conflicts     │
├─────────────────────────────────────────────────┤
│  Layer 3: Domain Intelligence (pluggable)        │
│  ┌─────────┐ ┌──────────┐ ┌────────────┐        │
│  │Capacity │ │Compliance│ │Expiration  │        │
│  │Dashboard│ │Reporter  │ │Tracker     │        │
│  └─────────┘ └──────────┘ └────────────┘        │
└─────────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Generate synthetic test data
python generate_test_data.py

# 3a. Run the Streamlit dashboard (recommended for demo)
streamlit run frontend/dashboard.py

# 3b. Or run the FastAPI backend
uvicorn app.main:app --reload
# Then visit http://localhost:8000/docs for the API

# 4. Run tests
pytest tests/ -v
```

## Key Design Decisions

### Entity Resolution (the star feature)
- **Weighted scoring**: license number match (50%), fuzzy name match (30%), facility + role agreement (20%)
- **Fuzzy matching**: uses `rapidfuzz` (token_sort_ratio) to handle name variations like "REYES, SOFIA" ↔ "Sofia Reyes"
- **Every decision is logged**: you can trace exactly why two records were merged and with what confidence

### Auditability
- Every raw record is preserved alongside its normalized version
- Every merge/resolution decision includes a confidence score and reason
- The compliance report traces every number back to exact source records

### Domain-agnostic core
- Layers 1 and 2 (ingestion + reconciliation) are industry-agnostic
- Layer 3 is a pluggable `DomainPlugin` — swap `HealthcareDomainPlugin` for `ConstructionPlugin` or `LogisticsPlugin` without changing anything else
- The schema registry and alias tables are configurable JSON, not hardcoded

### Handles messy data gracefully
- Auto-detects CSV column mappings via fuzzy header matching
- Normalizes dates across multiple formats
- Flags unknown values instead of crashing — surfaces them for human review

## File Structure

```
pulse-foundry-ssot/
├── README.md
├── requirements.txt
├── generate_test_data.py          # Synthetic data with intentional messiness
├── app/
│   ├── main.py                    # FastAPI entry point
│   ├── ingest/
│   │   ├── csv_loader.py          # CSV parsing + auto-schema detection
│   │   ├── pdf_loader.py          # PDF schedule extraction (pdfplumber)
│   │   └── normalizer.py          # Name/facility/date/phone normalization
│   ├── reconcile/
│   │   ├── entity_resolver.py     # Union-find entity resolution
│   │   ├── conflict_detector.py   # Mismatch/missing/stale detection
│   │   └── confidence.py          # Weighted match scoring
│   ├── domain/
│   │   ├── base.py                # DomainPlugin abstract interface
│   │   └── healthcare/
│   │       ├── capacity.py        # Module A: staffing dashboard
│   │       ├── compliance.py      # Module B: regulatory reports
│   │       └── expiration.py      # Module C: credential tracker
│   ├── models/
│   │   └── entities.py            # Core data models
│   └── api/
│       └── routes.py              # REST API endpoints
├── frontend/
│   └── dashboard.py               # Streamlit UI
├── config/
│   ├── aliases.json               # Facility/job code mappings
│   └── schema_registry.json       # Column auto-detection rules
└── tests/
    └── test_reconciliation.py     # Unit + integration tests
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload` | Upload a CSV or PDF file |
| POST | `/api/reconcile` | Run entity resolution + conflict detection |
| GET | `/api/reports/capacity` | Staffing capacity dashboard |
| GET | `/api/reports/compliance` | Compliance / staffing summary report |
| GET | `/api/reports/expirations` | License expiration tracker |
| GET | `/api/status` | Pipeline status |
| GET | `/api/audit/persons` | Full person audit trail |
| POST | `/api/reset` | Clear all data |

## Reusability Story

To adapt this for a different industry (e.g., construction):

1. Create `app/domain/construction/` with modules for safety certifications, equipment tracking, and crew scheduling
2. Implement the `DomainPlugin` interface
3. Update `config/aliases.json` with industry-specific codes
4. Update `config/schema_registry.json` with new column mappings
5. **Layers 1 and 2 stay exactly the same** — ingestion, normalization, and entity resolution are universal
