"""Agora backend — FastAPI entrypoint."""

from fastapi import FastAPI

app = FastAPI(title="Agora", description="Multi-agent debate & evaluation platform")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
