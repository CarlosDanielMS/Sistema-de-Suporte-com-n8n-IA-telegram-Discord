# Sistema de Suporte com IA — Projeto Final

Atendimento automatizado de tickets com RAG, agente Python, avaliação RAGAS e observabilidade via LangSmith — orquestrado pelo n8n rodando localmente.

---

## Visão geral do fluxo

```
[Usuário]
    │
    ▼
[Google Forms]          ← entrada do ticket (link fixo, sem setup de infra)
    │
    ▼
[Google Sheets]         ← respostas do formulário salvas automaticamente
    │
    ▼
[n8n — Google Sheets Trigger]  ← detecta novas linhas a cada 1 minuto
    │
    ▼
[n8n — HTTP Request]    ← chama o agente Python via POST /perguntar
    │
    ▼
[Agente Python]
    ├── RAG (ChromaDB)  ← busca trechos relevantes da documentação
    ├── classifica urgência (alta / média / baixa)
    └── gera resposta com fontes citadas
    │
    ▼
[n8n — Router]          ← roteia pelo campo `urgencia` da resposta
    ├── urgência alta   → Discord (webhook do canal)
    └── urgência normal → Google Sheets (registro + resposta)
    │
    ▼ (semanalmente, via script local)
[Script RAGAS]          ← lê golden_set.json e calcula métricas em batch
    │
    ▼
[LangSmith]             ← trace de cada execução do agente, ao vivo
```

---

## Estrutura de arquivos

```
projeto-final/
├── documentos/              ← coloque aqui os PDFs a serem indexados
├── chroma_db/               ← banco vetorial (gerado pelo script de ingestão)
├── src/
│   ├── __init__.py
│   ├── ingestao.py          ← carrega PDFs, gera chunks e indexa no ChromaDB
│   ├── agente.py            ← lógica do agente (RAG + classificação de urgência)
│   └── api.py               ← servidor FastAPI com endpoint /perguntar
├── avaliacao/
│   ├── golden_set.json      ← perguntas + respostas esperadas para testes
│   └── rodar_ragas.py       ← script de avaliação batch com RAGAS
├── workflow/
│   └── suporte_n8n.json     ← workflow pronto para importar no n8n
├── .env.example             ← template de variáveis de ambiente (versionar)
├── .env                     ← chaves reais (NÃO versionar — está no .gitignore)
├── .gitignore
└── requirements.txt
```

---

## Componentes

### 1. Entrada — Google Forms + Google Sheets Trigger

O usuário preenche o formulário abaixo com nome, descrição do problema e categoria:

**[Abrir formulário](https://docs.google.com/forms/d/198M0XWcOlQDi9AL200Bwh-44ceYPiRBJuEkekyFelTo/viewform)**

As respostas vão automaticamente para uma aba do Google Sheets vinculada ao formulário. O n8n usa o trigger **Google Sheets** para detectar novas linhas a cada minuto — sem precisar de Apps Script ou IP público.

> **Passo manual:** configurar as credenciais do Google Sheets no n8n e vincular o trigger à planilha de respostas do formulário — veja [Passo 3](#passo-3--google-sheets) e [Passo 4](#passo-4--entrada-via-google-forms).

### 2. Orquestrador — n8n (local via Docker)

Roda localmente sem nenhuma conta:

```bash
docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n n8nio/n8n
```

Acesse em `http://localhost:5678`. Importe o arquivo `workflow/suporte_n8n.json` pelo menu **Workflows → Import from file**.

**Workflow principal:**

```
Suporte TI (Google Sheets Trigger — polling a cada 1 min)
    → Chamar Agente (POST http://host.docker.internal:8000/perguntar)
    → Urgência Alta? (IF urgencia == "alta")
        TRUE  → Notificar Discord (HTTP POST webhook)
        FALSE → Salvar no Sheets (Google Sheets append)
```

> `host.docker.internal` é o endereço do seu computador visto de dentro do container Docker. Funciona no Windows e macOS com Docker Desktop. No Linux, substitua pelo IP da máquina (geralmente `172.17.0.1`).

### 3. Agente Python — `src/agente.py`

Recebe a pergunta, executa as ferramentas e devolve resposta estruturada.

**Ferramentas do agente:**

- `buscar_documentos_internos` — faz busca vetorial no ChromaDB e retorna os 3 chunks mais relevantes com fonte e página
- `classificar_urgencia` — usa `llama-3.3-70b-versatile` (via Groq) para classificar a urgência como `alta`, `media` ou `baixa`

O agente usa `llama-3.3-70b-versatile` (via Groq) com function calling (via `create_tool_calling_agent` do LangChain). LangSmith fica ativo automaticamente pelas variáveis de ambiente — sem mudar uma linha de código.

> **Por que Groq?** A Groq oferece inferência extremamente rápida (LPU) com plano gratuito generoso. Não exige cartão de crédito para começar e os modelos Llama 3 têm qualidade comparável ao GPT-4o para tarefas de suporte.

### 4. API FastAPI — `src/api.py`

Servidor HTTP que expõe o agente.

**Endpoint:**

```
POST /perguntar
{
  "pergunta": "Como resetar a senha?",
  "usuario_id": "joao@empresa.com"
}
```

**Resposta:**

```json
{
  "resposta": "Para resetar a senha, acesse...",
  "fontes": ["manual_ti.pdf — p. 12"],
  "urgencia": "baixa",
  "tools_utilizadas": ["buscar_documentos_internos", "classificar_urgencia"],
  "latencia_s": 2.1
}
```

### 5. RAG — `src/ingestao.py` + ChromaDB

PDFs da pasta `documentos/` são divididos em chunks de 512 tokens com overlap de 50, transformados em embeddings locais (`paraphrase-multilingual-MiniLM-L12-v2` via `sentence-transformers` — **gratuito, roda offline, suporta português**) e salvos no ChromaDB local em `chroma_db/`.

Na consulta, o agente gera o embedding da pergunta, busca os 3 chunks mais próximos e os envia como contexto ao LLM.

> O modelo de embeddings roda 100% local — não envia seus documentos para nenhuma API externa.

### 6. Saída — Discord (urgente) ou Google Sheets (normal)

**Discord:** o n8n faz um `HTTP POST` simples para a URL do webhook do canal. Sem autenticação, sem conta paga.

**Google Sheets:** o nó nativo do n8n adiciona uma linha com os campos:
`timestamp | usuario | pergunta | resposta | urgencia | fontes | latencia_s`

### 7. Avaliação — `avaliacao/rodar_ragas.py`

Script que roda o agente sobre as perguntas do `golden_set.json` e calcula:

| Métrica           | O que mede                               | Meta  |
| ----------------- | ---------------------------------------- | ----- |
| Faithfulness      | Resposta baseada nos chunks recuperados? | > 0.7 |
| Answer Relevancy  | Resposta responde à pergunta?            | > 0.7 |
| Context Precision | Chunks recuperados eram úteis?           | > 0.6 |

### 8. Observabilidade — LangSmith

Ativado apenas pelas variáveis de ambiente no `.env`. Cada chamada ao agente aparece no dashboard com hierarquia completa (AgentExecutor → LLM calls → Tool calls), latência e chunks recuperados.

---

## Como rodar

### Pré-requisitos

- Python 3.11+
- Docker Desktop (para o n8n)
- Conta Groq (gratuita — console.groq.com)
- Conta LangSmith (gratuita — smith.langchain.com)

### 1. Criar e ativar o ambiente virtual

```bash
# na pasta projeto-final/
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 2. Instalar dependências

```bash
pip install -r requirements.txt
```

### 3. Configurar variáveis de ambiente

```bash
# copie o template
cp .env.example .env

# edite .env e preencha:
# GROQ_API_KEY, LANGCHAIN_API_KEY, DISCORD_WEBHOOK_URL, GOOGLE_SHEET_ID
```

### 4. Adicionar documentos e indexar

Coloque os PDFs da sua documentação em `documentos/` e rode:

```bash
python src/ingestao.py
```

Isso cria a pasta `chroma_db/` com os embeddings. Rode novamente sempre que adicionar novos PDFs.

### 5. Subir a API do agente

```bash
uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
```

Teste se está funcionando:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

### 6. Subir o n8n e configurar o workflow

```bash
docker run -it --rm -p 5678:5678 -v n8n_data:/home/node/.n8n n8nio/n8n
```

Acesse `http://localhost:5678`. Importe `workflow/suporte_n8n.json` pelo menu **Workflows → Import from file** e, em seguida, preencha os placeholders em cada nó:

#### Nó "Suporte TI" (Google Sheets Trigger)

| Campo | O que preencher |
|---|---|
| **Credential for Google Sheets OAuth2 API** | Selecione `Google Sheets` (criada no Passo 3.2) |
| **Trigger On** | `Row Added` (já preenchido) |
| **Document** | ID já preenchido (`198M0XWcOlQDi9AL200Bwh-44ceYPiRBJuEkekyFelTo`) — clique na seta para confirmar que carrega |
| **Sheet** | Selecione no dropdown — o nome está no rodapé da planilha no Google Sheets |

> Instruções detalhadas de configuração no Passo 4.2.

#### Nó "Chamar Agente" (HTTP Request)

Normalmente não precisa alterar. Verifique apenas:

| Campo | Valor esperado |
|---|---|
| **URL** | `http://host.docker.internal:8000/perguntar` (Windows/macOS) — no Linux troque por `http://172.17.0.1:8000/perguntar` |
| **Body** | Os campos `$json['Descrição do problema']` e `$json['Nome / identificação']` devem bater com os nomes das colunas da planilha; se os nomes das perguntas do formulário forem diferentes, ajuste aqui |

#### Nó "Notificar Discord" (HTTP Request)

| Campo | O que preencher |
|---|---|
| **URL** | Cole a URL do webhook do Discord (formato `https://discord.com/api/webhooks/...`) |

#### Nó "Salvar no Sheets" (Google Sheets)

| Campo | O que preencher |
|---|---|
| **Credential for Google Sheets OAuth2 API** | Selecione `Google Sheets` (mesma credencial do Passo 3.2) |
| **Document** | ID da planilha de saída (onde ficam os tickets processados) — substitua `COLE_AQUI_O_ID_DA_PLANILHA_DE_SAIDA` |
| **Sheet** | `Tickets` (crie essa aba na planilha de saída se ainda não existir) |

Após preencher tudo, clique em **Save** e depois em **Activate** (toggle no canto superior direito do workflow).

### 7. Testar o fluxo completo

1. Com o workflow ativo no n8n, preencha o formulário: **[abrir formulário](https://docs.google.com/forms/d/198M0XWcOlQDi9AL200Bwh-44ceYPiRBJuEkekyFelTo/viewform)**
2. Em até 1 minuto, o n8n detecta a nova linha e dispara o fluxo
3. Acompanhe a execução em `http://localhost:5678` → **Executions** → clique na execução mais recente
4. Se `urgencia` for `alta`, a mensagem aparece no Discord; caso contrário, a linha é adicionada na planilha de saída

Para testar o agente diretamente (sem passar pelo n8n):

```bash
curl -X POST http://localhost:8000/perguntar \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "Como solicito férias?", "usuario_id": "maria@empresa.com"}'
```

### 8. Rodar avaliação RAGAS

```bash
python avaliacao/rodar_ragas.py
```

Roda o agente sobre as 5 perguntas do `golden_set.json` e imprime as métricas. Edite o arquivo para adicionar mais perguntas representativas do seu caso de uso.

---

## Passos manuais

Estes são os únicos passos que não podem ser automatizados — tudo o mais já está no código.

### Passo 1 — Chave de API da Groq

1. Acesse [console.groq.com](https://console.groq.com) e crie uma conta (gratuita, sem cartão)
2. No painel, clique em **API Keys** no menu lateral esquerdo
3. Clique em **Create API Key**, dê um nome (ex: `laboratorio-ia`) e confirme
4. Copie a chave gerada — ela começa com `gsk_...` e **só aparece uma vez**
5. Cole em `GROQ_API_KEY` no seu arquivo `.env`

| Onde criar                                                               | O que copiar    | Onde colar no `.env` |
| ------------------------------------------------------------------------ | --------------- | -------------------- |
| [console.groq.com](https://console.groq.com) → API Keys                  | Chave `gsk_...` | `GROQ_API_KEY`       |
| [smith.langchain.com](https://smith.langchain.com) → Settings → API Keys | Chave `ls__...` | `LANGCHAIN_API_KEY`  |

---

### Passo 1.1 — Chave de API do LangSmith (observabilidade)

O LangSmith é usado para rastrear todas as chamadas ao agente — você vê latência, tokens, prompts e erros em tempo real, sem mudar nada no código.

#### 1.1.1 — Criar conta e projeto

1. Acesse [smith.langchain.com](https://smith.langchain.com) e clique em **Sign Up**
2. Crie a conta com Google, GitHub ou e-mail (plano gratuito não exige cartão)
3. Após o login, você cai na tela inicial. Clique em **+ New Project** (canto superior direito)
4. Dê o nome `laboratorio-ia` (ou qualquer nome descritivo) e clique em **Create**
5. Guarde o nome do projeto — ele vai para a variável `LANGCHAIN_PROJECT` no `.env`

#### 1.1.2 — Gerar a chave de API

1. No menu lateral esquerdo, clique no ícone de engrenagem (**Settings**) — fica no rodapé da barra
2. Selecione a aba **API Keys**
3. Clique em **Create API Key**
4. Em **Description**, escreva algo como `laboratorio-ia-key`
5. Clique em **Create API Key** — a chave gerada começa com `ls__...`
6. **Copie imediatamente** — ela só aparece uma vez nessa tela
7. Cole em `LANGCHAIN_API_KEY` no seu arquivo `.env`

#### 1.1.3 — Configurar o `.env`

Verifique se as três variáveis do LangSmith estão preenchidas no `.env`:

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_API_KEY=ls__xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LANGCHAIN_PROJECT=laboratorio-ia
```

> `LANGCHAIN_TRACING_V2=true` é o interruptor principal — sem ele, nenhum trace é enviado mesmo com a chave configurada.

#### 1.1.4 — Verificar se os traces estão chegando

1. Suba a API (`uvicorn src.api:app --reload`) e envie uma pergunta de teste:
   ```bash
   curl -X POST http://localhost:8000/ticket \
     -H "Content-Type: application/json" \
     -d '{"pergunta": "Teste LangSmith", "usuario_id": "teste@empresa.com"}'
   ```
2. Volte ao LangSmith → seu projeto `laboratorio-ia` → aba **Traces**
3. Em alguns segundos aparece uma linha com a execução. Clique nela para ver:
   - O prompt enviado ao LLM
   - A resposta gerada
   - Latência de cada passo (recuperação, chamada ao modelo, pós-processamento)
   - Tokens consumidos

> Se após 30 segundos não aparecer nada, confirme que `LANGCHAIN_TRACING_V2=true` está no `.env` **sem espaços em branco** e que a API foi reiniciada após editar o arquivo.

---

### Passo 2 — Discord: servidor, canal e webhook

Esta é a parte mais detalhada. Siga os sub-passos na ordem.

#### 2.1 — Criar um servidor Discord (se ainda não tiver um)

1. Abra o Discord (app ou [discord.com/app](https://discord.com/app) no navegador)
2. Na barra lateral esquerda, clique no ícone **+** (Adicionar um servidor) — fica no fim da lista de servidores
3. Selecione **Criar o meu** → **Para mim e meus amigos**
4. Dê um nome ao servidor (ex: `Suporte TI Lab`) e clique em **Criar**
5. Seu servidor aparece na barra lateral. Clique nele para abri-lo.

> Se já tiver um servidor onde você é **administrador**, pode usá-lo diretamente — pule para o sub-passo 2.2.

#### 2.2 — Criar um canal de texto para alertas

1. Dentro do servidor, passe o mouse sobre a categoria **CANAIS DE TEXTO** — aparecem dois ícones à direita: um **+** e uma engrenagem
2. Clique no **+** para criar um canal novo
3. Em **Tipo de canal**, deixe selecionado **Canal de texto** (o padrão)
4. Em **Nome do canal**, digite algo como `alertas-urgentes` (sem espaços, minúsculas)
5. Clique em **Criar canal**

#### 2.3 — Criar o webhook no canal

1. Clique com o botão direito no canal `alertas-urgentes` e selecione **Editar canal**
   - Alternativa: clique na engrenagem que aparece ao passar o mouse sobre o canal
2. No menu lateral da tela de configurações, selecione **Integrações**
3. Clique em **Webhooks** → depois em **Criar Webhook**
4. Um webhook chamado "Capitão Gancho" é criado automaticamente
5. Personalize se quiser:
   - **Nome**: altere para `Bot de Suporte` (é o nome que aparece nas mensagens)
   - **Canal**: confirme que está no canal correto (`alertas-urgentes`)
   - **Ícone**: opcional — você pode fazer upload de uma imagem
6. Clique em **Copiar URL do webhook** — a URL tem o formato:
   ```
   https://discord.com/api/webhooks/1234567890123456789/xxxxxxxxxxxxxxxxxxxx
   ```
7. Clique em **Salvar alterações**

> **Guarde bem essa URL.** Ela funciona como uma senha — qualquer pessoa com essa URL pode enviar mensagens no canal. Não a compartilhe publicamente.

#### 2.4 — Testar o webhook antes de integrar

Antes de configurar no n8n, confirme que o webhook funciona mandando uma mensagem de teste:

**No terminal (Windows PowerShell):**

```powershell
$body = '{"content": "Teste de webhook — tudo funcionando!"}'
Invoke-RestMethod -Uri "SUA_URL_AQUI" -Method Post -Body $body -ContentType "application/json"
```

**No terminal (macOS / Linux / Git Bash):**

```bash
curl -X POST "SUA_URL_AQUI" \
  -H "Content-Type: application/json" \
  -d '{"content": "Teste de webhook — tudo funcionando!"}'
```

Se der certo, a mensagem aparece no canal `alertas-urgentes` em segundos. O retorno do curl/PowerShell deve ser vazio (204 No Content) — isso é correto.

#### 2.5 — Configurar no `.env` e no n8n

1. Abra seu arquivo `.env` e cole a URL:
   ```
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/SEU_ID/SEU_TOKEN
   ```
2. No n8n, abra o workflow importado e localize o nó **Notificar Discord**
3. No campo **URL**, substitua o placeholder pela mesma URL do webhook
4. Salve o nó e ative o workflow

#### 2.6 — Formato da mensagem no Discord (opcional)

O n8n envia um `HTTP POST` com corpo JSON. O campo `content` é o texto da mensagem. Para mensagens mais ricas, você pode usar o campo `embeds`:

```json
{
  "content": "🚨 **Ticket urgente recebido!**",
  "embeds": [
    {
      "title": "Detalhes do ticket",
      "color": 16711680,
      "fields": [
        { "name": "Usuário", "value": "joao@empresa.com", "inline": true },
        { "name": "Urgência", "value": "alta", "inline": true },
        { "name": "Pergunta", "value": "Servidor caiu na produção!" }
      ]
    }
  ]
}
```

Configure isso no nó HTTP Request do n8n usando expressões para puxar os valores da resposta do agente.

---

### Passo 3 — Google Sheets

#### 3.1 — Criar a planilha de saída (tickets processados)

1. Crie uma planilha em [sheets.google.com](https://sheets.google.com)
2. Na primeira linha, crie as colunas: `timestamp | usuario | pergunta | resposta | urgencia | fontes | latencia_s`
3. Copie o ID da planilha (parte da URL entre `/d/` e `/edit`) e cole em `GOOGLE_SHEET_ID` no `.env`

#### 3.2 — Criar credencial Google Sheets OAuth2 no n8n

O n8n 1.x usa **OAuth2** para o Google Sheets. Funciona normalmente em localhost porque o redirect de autenticação volta para `http://localhost:5678` — não precisa de IP público.

O processo tem duas partes: criar um "app" no Google Cloud para obter as chaves OAuth2, depois conectar esse app no n8n.

---

**Passo 3.2.1 — Criar um projeto no Google Cloud**

1. Acesse [console.cloud.google.com](https://console.cloud.google.com) e faça login
2. Clique no seletor de projetos (topo da página) → **Novo projeto**
3. Nome: `laboratorio-ia` → **Criar**
4. Confirme que o projeto `laboratorio-ia` está selecionado no topo

---

**Passo 3.2.2 — Ativar a Google Sheets API**

1. Menu lateral → **APIs e serviços → Biblioteca**
2. Busque `Google Sheets API` → clique no resultado → **Ativar**

---

**Passo 3.2.3 — Configurar a tela de consentimento OAuth**

1. Menu lateral → **APIs e serviços → Tela de consentimento OAuth**
2. Selecione **Externo** → **Criar**
3. Preencha apenas os campos obrigatórios:
   - **Nome do app**: `n8n-laboratorio`
   - **E-mail de suporte ao usuário**: seu e-mail
   - **E-mail do desenvolvedor** (rodapé): seu e-mail
4. Clique em **Salvar e continuar** nas próximas telas até chegar em **Voltar ao painel**
5. **Não clique em "Publicar app"** — deixe o app em modo de teste
6. Role até a seção **Usuários de teste** e clique em **+ Add Users**
7. Adicione o e-mail da conta Google que vai autorizar no n8n (o mesmo que você usa no Google Sheets)
8. Clique em **Salvar**

> Apps em modo de teste só funcionam para e-mails listados em "Usuários de teste". Se ao clicar em "Sign in with Google" no n8n aparecer erro 403 "access_denied", é porque o e-mail não foi adicionado aqui.

---

**Passo 3.2.4 — Criar as credenciais OAuth2**

1. Menu lateral → **APIs e serviços → Credenciais**
2. Clique em **+ Criar credenciais → ID do cliente OAuth**
3. **Tipo de aplicativo**: `Aplicativo da Web`
4. **Nome**: `n8n-local`
5. Em **URIs de redirecionamento autorizados**, clique em **+ Adicionar URI** e cole:
   ```
   http://localhost:5678/rest/oauth2-credential/callback
   ```
6. Clique em **Criar**
7. Uma janela mostra o **ID do cliente** e o **Segredo do cliente** — **copie os dois** (você pode fechar e ver novamente depois em Credenciais → nome do cliente)

---

**Passo 3.2.5 — Adicionar a credencial no n8n**

1. Acesse `http://localhost:5678` → clique no ícone do usuário (canto superior direito) → **Settings → Credentials**
2. Clique em **Create credential** (botão laranja, canto superior direito)
3. Na caixa de busca, digite `Google Sheets` → selecione **Google Sheets OAuth2 API**
4. Preencha:
   - **Name**: `Google Sheets`
   - **Client ID**: cole o ID do cliente copiado no passo anterior
   - **Client Secret**: cole o segredo do cliente
5. Clique em **Sign in with Google** (ou **Connect**) — abre uma janela do Google para você autorizar com sua conta
6. Selecione sua conta Google e clique em **Continuar** (pode aparecer aviso de "app não verificado" — clique em **Avançado → Acessar n8n-laboratorio**)
7. Após autorizar, a janela fecha e o n8n mostra a credencial como **Connected** ✓
8. Clique em **Save**

#### 3.3 — Vincular a credencial ao nó "Salvar no Sheets"

1. No workflow importado, clique no nó **Salvar no Sheets**
2. No campo **Credential for Google Sheets OAuth2 API**, selecione `Google Sheets`
3. O Document ID já está preenchido — confirme que a planilha existe e tem uma aba chamada `Tickets`
4. Salve o nó

### Passo 4 — Entrada via Google Forms

O formulário já está criado: **[abrir formulário](https://docs.google.com/forms/d/198M0XWcOlQDi9AL200Bwh-44ceYPiRBJuEkekyFelTo/viewform)**

O Google Forms salva cada resposta automaticamente numa aba do Google Sheets. O n8n detecta novas linhas via polling (a cada minuto) — não é necessário Apps Script nem IP público.

#### 4.1 — Encontrar a planilha de respostas do formulário

1. Abra o formulário em modo de edição → aba **Respostas** → clique no ícone de planilha verde (**Vincular ao Sheets**)
2. Se ainda não houver planilha vinculada, clique em **Criar** — o Google cria uma planilha nova com uma aba chamada `Respostas ao formulário 1`
3. Copie o ID dessa planilha (URL entre `/d/` e `/edit`)

#### 4.2 — Configurar o nó "Suporte TI" no n8n

1. No workflow importado, clique duas vezes no nó **Suporte TI** (ícone de planilha verde com um raio)
2. Um painel abre à direita com os seguintes campos — preencha assim:

   | Campo no n8n | O que fazer |
   |---|---|
   | **Credential for Google Sheets OAuth2 API** | Selecione `Google Sheets` (criada no Passo 3.2) |
   | **Trigger On** | Deixe como `Row Added` |
   | **Document** | O ID já está preenchido (`198M0XWcOlQDi9AL200Bwh-44ceYPiRBJuEkekyFelTo`) — clique no ícone de seta para carregar a planilha |
   | **Sheet** | Selecione a aba no dropdown — o nome exato está no rodapé da planilha (ex: `Respostas ao formulário 1` ou o nome que o Google criou automaticamente) |

3. Clique em **Save**
4. No canto superior direito do workflow, clique no toggle **Inactive → Active** para ativar

> Quando o workflow estiver ativo, o n8n verifica a cada minuto se há novas linhas. Ao detectar, dispara o fluxo completo automaticamente.

#### 4.4 — Mapear os campos do formulário

O nó **Chamar Agente** espera dois campos: `pergunta` e `usuario_id`. Eles são mapeados a partir das colunas da planilha, que têm o mesmo nome das perguntas do formulário.

No nó **Chamar Agente**, confirme que o body está assim (ajuste os nomes de coluna se as perguntas do formulário tiverem texto diferente):

```json
{
  "pergunta": "={{ $json['Descrição do problema'] }}",
  "usuario_id": "={{ $json['Nome / identificação'] }}"
}
```

> Para ver os nomes exatos das colunas: preencha o formulário uma vez, depois no n8n clique em **Execute workflow** e inspecione o output do nó **Suporte TI**.

### Passo 5 — Adicionar PDFs em `documentos/`

Coloque os manuais, FAQs e documentações internas em `documentos/` e rode novamente:

```bash
python src/ingestao.py
```

O ChromaDB é incremental — PDFs já indexados não são re-processados se você usar `get_or_create_collection`.

---

## Decisões de implementação

| Decisão             | Escolha                       | Motivo                                                       |
| ------------------- | ----------------------------- | ------------------------------------------------------------ |
| LLM                 | Groq (Llama 3.3 70B)          | Gratuito, rápido (LPU), sem necessidade de cartão            |
| Embeddings          | sentence-transformers (local) | Gratuito, offline, documentos não saem da máquina            |
| Entrada             | Google Forms + Sheets Trigger | Sem IP público, sem Apps Script, familiar para usuários      |
| Orquestrador        | n8n local (Docker)            | Self-hosted, gratuito, visual                                |
| Agente              | LangChain + FastAPI           | Controle total, integração nativa com LangSmith              |
| Banco vetorial      | ChromaDB local                | Zero infra, suficiente para volumes pequenos/médios          |
| Notificação urgente | Discord webhook               | URL gerada em 10 segundos, sem autenticação                  |
| Avaliação           | RAGAS sobre golden set        | Não bloqueia o fluxo principal, custo controlado             |
| Observabilidade     | LangSmith                     | Integração nativa com LangChain, gratuito até certo volume   |
| Ambiente Python     | venv + requirements.txt       | Isolamento de dependências, reprodutível em qualquer máquina |

---

## Status

- [ ] `.env` preenchido com as chaves reais (Groq + LangSmith)
- [ ] PDFs adicionados em `documentos/` e indexados (`python src/ingestao.py`)
- [ ] Agente respondendo em `http://localhost:8000/perguntar`
- [ ] n8n rodando localmente (`docker run ...`)
- [ ] Service Account do Google criada e chave JSON salva
- [ ] Planilha de saída ("Tickets") criada e compartilhada com a Service Account
- [ ] Planilha de respostas do formulário compartilhada com a Service Account
- [ ] Credencial "Google Sheets" configurada no n8n (Passo 3.2)
- [ ] Workflow importado no n8n (`workflow/suporte_n8n.json`)
- [ ] Nó "Suporte TI" apontando para a planilha de respostas do formulário
- [ ] Nó "Salvar no Sheets" apontando para a planilha de saída
- [ ] Servidor Discord criado com canal `alertas-urgentes`
- [ ] Webhook do Discord criado, testado e URL no `.env`
- [ ] Nó "Notificar Discord" apontando para a URL do webhook
- [ ] Formulário preenchido e fluxo ponta a ponta funcionando
- [ ] LangSmith com traces visíveis em smith.langchain.com
- [ ] Golden set com pelo menos 10 perguntas reais
- [ ] Script RAGAS executando sem erros (`python avaliacao/rodar_ragas.py`)
