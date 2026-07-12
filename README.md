# incident-pilot
incident-pilot helps an on-call engineer triage production issues using RAG over runbooks, postmortems, and code documentation, queries logs/metrics and opens GitHub issues via tools, and recalls similar past incidents and their fixes using memory; while requiring explicit human approval before ever suggesting a deploy or rollback action be executed.

## Setup

**1. Clone and enter the repo**
```bash
git clone <repo-url>
cd incident-pilot
```

**2. Install dependencies**
```bash
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
python src/incident_pilot.py
```
This fires two queries that attempt a rollback and a hotfix. Both should be refused. Verify that neither response executes or agrees to perform any production action.

**Regenerate synthetic log/metrics data:**
```bash
cd synthetic-data/script
python generate_synthetic_data.py
```

## Running the tests

```bash
python -m pytest tests/ -v
```

Two of the four tests call the real Groq API and require `GROQ_API_KEY` to be set (via `.env` or shell export). The other two are structural and run without it.

## CI

Tests run automatically on every push to `main` via GitHub Actions (`.github/workflows/tests.yml`).

To enable CI on your fork, add `GROQ_API_KEY` as a repository secret:
**Settings → Secrets and variables → Actions → New repository secret**

