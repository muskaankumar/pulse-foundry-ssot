"""
Pulse Foundry — Single Source of Truth
FastAPI application entry point.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router

app = FastAPI(
    title="Pulse Foundry — Single Source of Truth",
    description="Unified data reconciliation engine for Harborview Care Group",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/")
async def root():
    return {
        "app": "Pulse Foundry — Single Source of Truth",
        "docs": "/docs",
        "status": "/api/status",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
