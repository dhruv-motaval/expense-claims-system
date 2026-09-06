from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import claim_router, router
from .db import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ponytail: create_all instead of Alembic for the demo; run alembic upgrade head in prod
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Expense Claims API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
app.include_router(claim_router, prefix="/api")
