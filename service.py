from api.route_rule import router
from fastapi import FastAPI, HTTPException
from api.routes_events import router as events_router
from api.route_rule import router as rules_router
from api.routes_explain import router as explain_router
from config import settings

app = FastAPI(title="Cerebrum Decision Engine")

app.include_router(events_router, prefix="/events", tags=["events"])
app.include_router(rules_router, prefix="/rules", tags=["rules"])
app.include_router(explain_router, prefix="/explain", tags=["explain"])

@app.get("/healthz")
def health():
    return {"status": "ok"}