# Ingestion Problem Statement: Enterprise-Grade Incident Triaging

## Background
During high-pressure production incidents (e.g., service degradations at 2 AM), on-call Site Reliability Engineers (SREs like Alex Kim) need immediate access to verified, up-to-date, and secure context. The **IncidentPilot** copilot aims to solve this by providing RAG‑grounded triage summaries using historical runbooks, postmortems, and internal documentation.

---

## Core Problems in Ingestion

### 1. Scattered and Unstructured Data Sources
Internal SRE documents do not exist in a single repository. They are spread across:
* **Platforms**: Confluence spaces, local filesystems, Slack threads, Jira tickets, and cloud storage.
* **Formats**: Markdown, PDF postmortems, HTML pages, and DOCX outlines.

Without a standardized normalization and ingestion pipeline, the system cannot reliably extract and chunk structured content (like tables, columnar layouts, and headers), leading to degraded retrieval quality.

### 2. Lack of Strict Security & Governance (RBAC)
In an enterprise, not all engineers have the same level of access:
* Some runbooks or postmortems contain sensitive details (e.g., security remediation steps, network topology, or customer PII) restricted to specific SRE teams.
* A naive RAG ingestion pipeline embeds and stores documents globally, presenting a major risk of **privilege escalation** where unauthorized users retrieve sensitive data through LLM prompts.
* Chunks must be stamped with granular permissions (ACLs) matching Active Directory or LDAP roles, and vector search must support strict, metadata‑level pre‑filtering.

### 3. Missing Metadata and Lack of Traceability
To ensure trust, on-call engineers must be able to verify where the LLM got its instructions:
* **No Citations**: Chunks without document IDs, section headers, or direct links (`source_url`) cannot be cited, forcing the engineer to guess if the recommendation is verified or an LLM hallucination.
* **Outdated Context**: Without modification tracking (`last_updated` and `checksum` hashes), the vector store cannot detect document updates, serving stale, outdated, or broken runbooks during an outage.

### 4. Poor Scalability and Pipeline Coupling
The prototype ingestion pipeline is tightly coupled and procedural:
* Adding a new document format (like a PDF) or a new source (like Google Drive) requires modifying core ingestion code.
* Re‑indexing the entire database for every change is resource‑intensive, slow, and does not support incremental syncing.

---

## Proposed Objective
To build a **modular, decoupled, and secure ingestion pipeline** for IncidentPilot that normalizes diverse formats, tags chunks with strict security permissions and traceability metadata (complying with the Standard Internal Schema), and feeds an RBAC‑filtered vector database for secure, verified, and cited retrieval.
