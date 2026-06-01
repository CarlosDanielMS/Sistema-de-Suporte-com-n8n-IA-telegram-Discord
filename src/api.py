from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv
from src.agente import executar, inicializar

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    inicializar()
    yield


app = FastAPI(title="Suporte IA", version="1.0", lifespan=lifespan)


class TicketIn(BaseModel):
    pergunta: str
    usuario_id: str = "anonimo"


class TicketOut(BaseModel):
    resposta: str
    fontes: list[str]
    urgencia: str
    tools_utilizadas: list[str]
    latencia_s: float


@app.post("/perguntar", response_model=TicketOut)
def perguntar(req: TicketIn):
    try:
        return executar(req.pergunta, req.usuario_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
def health():
    return {"status": "ok"}
