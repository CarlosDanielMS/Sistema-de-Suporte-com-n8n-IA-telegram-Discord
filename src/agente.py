import time
from pathlib import Path
from dotenv import load_dotenv
import chromadb
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_groq import ChatGroq
from langchain.tools import tool
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

load_dotenv()

CHROMA_DIR = "chroma_db"
COLLECTION = "documentacao"
TOP_K = 3

_collection = None
_embeddings = None
_agente: AgentExecutor | None = None


def _get_collection():
    global _collection, _embeddings
    if _collection is None:
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        _collection = client.get_or_create_collection(COLLECTION)
        _embeddings = HuggingFaceEmbeddings(model_name="paraphrase-multilingual-MiniLM-L12-v2")
    return _collection, _embeddings


@tool
def buscar_documentos_internos(pergunta: str) -> str:
    """Busca trechos relevantes da documentação interna para responder à pergunta."""
    col, emb = _get_collection()
    count = col.count()
    if count == 0:
        return "Nenhum documento encontrado. Adicione PDFs em documentos/ e rode: python src/ingestao.py"
    vec = emb.embed_query(pergunta)
    results = col.query(
        query_embeddings=[vec],
        n_results=min(TOP_K, count),
        include=["documents", "metadatas"],
    )
    trechos = []
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        fonte = Path(meta.get("source", "desconhecido")).name
        pagina = meta.get("page", "?")
        trechos.append(f"[{fonte} — p. {pagina}]\n{doc}")
    return "\n\n---\n\n".join(trechos) if trechos else "Nenhum documento relevante encontrado."


@tool
def classificar_urgencia(descricao: str) -> str:
    """Classifica a urgência do ticket como 'alta', 'media' ou 'baixa'."""
    llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
    resp = llm.invoke(
        "Classifique a urgência do ticket abaixo.\n"
        "Responda APENAS com uma palavra: alta, media ou baixa.\n\n"
        f"Ticket: {descricao}"
    )
    return resp.content.strip().lower()


def _get_agente() -> AgentExecutor:
    global _agente
    if _agente is None:
        llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)
        tools = [buscar_documentos_internos, classificar_urgencia]
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "Você é um agente de suporte técnico. "
                "Sempre use buscar_documentos_internos antes de responder. "
                "Use classificar_urgencia para determinar a prioridade. "
                "Cite as fontes no formato [arquivo — p. X] na resposta final.",
            ),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])
        agent = create_tool_calling_agent(llm, tools, prompt)
        _agente = AgentExecutor(
            agent=agent, tools=tools, verbose=False, return_intermediate_steps=True
        )
    return _agente


def inicializar():
    """Pré-carrega modelos e agente para que a primeira requisição não sofra cold start."""
    _get_collection()
    _get_agente()


def executar(pergunta: str, usuario_id: str = "anonimo") -> dict:
    agente = _get_agente()
    inicio = time.time()
    resultado = agente.invoke({"input": pergunta})
    latencia = round(time.time() - inicio, 2)

    tools_usadas, fontes, contexts, urgencia = [], [], [], "baixa"
    for action, observation in resultado.get("intermediate_steps", []):
        tools_usadas.append(action.tool)
        if action.tool == "buscar_documentos_internos":
            contexts.append(observation)
            for linha in observation.split("\n"):
                if linha.startswith("[") and "—" in linha:
                    fontes.append(linha[1:].split("]")[0])
        elif action.tool == "classificar_urgencia":
            urgencia = observation.strip()

    return {
        "resposta": resultado["output"],
        "fontes": list(dict.fromkeys(fontes)),
        "urgencia": urgencia,
        "tools_utilizadas": list(dict.fromkeys(tools_usadas)),
        "latencia_s": latencia,
        "contexts": contexts,
    }
