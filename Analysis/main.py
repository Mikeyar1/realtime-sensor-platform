"""
main.py — LabReplay Analysis API (FastAPI, port 8081)

Run:
    cd LabReplay/Analysis
    uv run python main.py
    # or: uvicorn main:app --port 8081 --reload

This file does exactly one thing: assemble the app from its routers.
All business logic lives in routers/ and drills/.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import health, sessions, trials, epoch, dissociation

app = FastAPI(title="LabReplay Analysis API", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(sessions.router)
app.include_router(trials.router)
app.include_router(epoch.router)
app.include_router(dissociation.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8081, reload=True)
