"""
FastAPI entry point with database persistence.

Drop-in replacement for main.py. The only differences:
  1. Creates tables on startup
  2. Imports routes_db instead of routes

Run with:  uvicorn app.main_db:app --reload
"""

from fastapi import FastAPI

from app.db.session import create_tables
from app.api.routes_db import router

app = FastAPI(
    title="Pulse Foundry — Single Source of Truth",
    description="Unified data reconciliation engine (database-backed)",
    version="2.0.0",
)

app.include_router(router, prefix="/api")


@app.on_event("startup")
def on_startup():
    create_tables()
