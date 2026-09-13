from fastapi import FastAPI

app = FastAPI(title="Nether Earth", version="0.0.0")


@app.get("/health", tags=["operations"])
def health() -> dict[str, str]:
    """Lightweight process health endpoint; contains no gameplay logic."""
    return {"status": "ok"}
