# incident-pilot
incident-pilot helps an on-call engineer triage production issues using RAG over runbooks, postmortems, and code documentation, queries logs/metrics and opens GitHub issues via tools, and recalls similar past incidents and their fixes using memory; while requiring explicit human approval before ever suggesting a deploy or rollback action be executed.

## Prerequisites

Before cloning, make sure you have the following installed on your machine:

**Python 3.11**
PyTorch (used for embeddings) does not have wheels for Python 3.13. Python 3.11 is required.

- macOS (Homebrew): `brew install python@3.11`
- Linux: `sudo apt install python3.11 python3.11-venv`
- Windows: download from [python.org/downloads](https://www.python.org/downloads/release/python-3119/)

Verify: `python3.11 --version` should print `Python 3.11.x`

**Groq API key**
The LLM runs on [Groq](https://console.groq.com). Create a free account and generate an API key at [console.groq.com/keys](https://console.groq.com/keys).

**Git**
`git --version` — install from [git-scm.com](https://git-scm.com) if missing.

---

## Setup

**1. Clone and enter the repo**
```bash
git clone <repo-url>
cd incident-pilot
```

**2. Create a Python 3.11 virtual environment and install dependencies**

PyTorch (required for embeddings) does not have wheels for Python 3.13. Use Python 3.11:
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

**3. Configure your API key**

Copy the example env file and fill in your [Groq API key](https://console.groq.com/keys):
```bash
cp .env.example .env
```
Then open `.env` and replace the placeholder:
```
GROQ_API_KEY=your_groq_api_key_here
```
Export it in your shell before running anything:
```bash
export GROQ_API_KEY=$(grep GROQ_API_KEY .env | cut -d= -f2)
```

## Running the agent

**Test the system prompt (guardrail verification):**
```bash
.venv/bin/python src/incident_pilot.py
```
This fires two queries that attempt a rollback and a hotfix. Both should be refused.

**Build the RAG vector store:**
```bash
.venv/bin/python src/ingestion.py
```
Deletes and recreates `synthetic-data/vectorstore/` from the current corpus. Run this whenever runbooks or postmortems change. Prints chunk count per document and top 3 results for a test query.

**Regenerate synthetic log/metrics data:**
```bash
cd synthetic-data/script
.venv/bin/python generate_synthetic_data.py
```

## Running the tests

```bash
.venv/bin/python -m pytest tests/ -v
```

Two of the four tests call the real Groq API and require `GROQ_API_KEY` to be set (via `.env` or shell export). The other two are structural and run without it.

## CI

Tests run automatically on every push to `main` via GitHub Actions (`.github/workflows/tests.yml`).

To enable CI on your fork, add `GROQ_API_KEY` as a repository secret:
**Settings → Secrets and variables → Actions → New repository secret**

