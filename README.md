<div align="center">
  <img src="https://i.ibb.co/4wy2C7fM/repo-icon-1782566943228.jpg" width="100" alt="Google Developers Logo">
  <h1>AI Agents Intensive: Ambient Expense Agent</h1>
  <p>
    <b>My portfolio submission for the Kaggle 5-Day AI Agents Intensive Vibecoding Course with Google</b>
  </p>
  <p>
    <a href="https://cloud.google.com/vertex-ai"><img src="https://img.shields.io/badge/Google%20Cloud-Vertex%20AI-4285F4?style=for-the-badge&logo=googlecloud" alt="Google Cloud"></a>
    <a href="https://adk.dev/"><img src="https://img.shields.io/badge/Agent%20Development%20Kit-v2.0-0F9D58?style=for-the-badge" alt="ADK"></a>
    <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-Dashboard-009688?style=for-the-badge&logo=fastapi" alt="FastAPI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=for-the-badge" alt="License"></a>
  </p>
</div>

---

## 📖 Overview

Welcome to my portfolio repository for the **Kaggle 5-Day AI Agents Intensive Vibecoding Course**. Over the course of this intensive, I utilized Google's cutting-edge Agentic IDE, **Antigravity**, to rapidly build, evaluate, and deploy an enterprise-scale AI agent using the **Agent Development Kit (ADK) 2.0**.

This project implements an **Ambient Expense Agent**—an autonomous, event-driven system that listens for expense reports via Google Cloud Pub/Sub, processes them through a secure ADK graph workflow hosted on Vertex AI Agent Runtime, and pauses for human intervention on high-risk expenses using a beautiful FastAPI manager dashboard on Cloud Run.

---

## 🏗️ Architecture at a Glance

The architecture leverages event-driven patterns to route incoming payloads directly to a stateful Agent Runtime execution engine.

```mermaid
flowchart TD
    %% Define Styles
    classDef gcp fill:#4285F4,color:white,stroke:#fff,stroke-width:2px;
    classDef agent fill:#0F9D58,color:white,stroke:#fff,stroke-width:2px;
    classDef dashboard fill:#F4B400,color:white,stroke:#fff,stroke-width:2px;
    classDef external fill:#DB4437,color:white,stroke:#fff,stroke-width:2px;

    ERP["Upstream System\n(e.g., ERP/Email)"]:::external -->|Publishes Event| PS["Google Cloud Pub/Sub\n(Topic: expense-reports)"]:::gcp
    
    PS -->|OIDC Push Subscription| AR["Vertex AI Agent Runtime\n(Stateful Engine)"]:::agent
    
    AR -->|"Auto-Approve (< $100)"| DB_Store[("Backend System\n(Approved)")]:::external
    
    AR -.->|Requires Manager Escalation\n(State: Paused)| DB_Session[("Agent Runtime\nSession Store")]:::gcp
    
    Manager["Human Manager"]:::external -->|Reviews| Dash["FastAPI Dashboard\n(Cloud Run)"]:::dashboard
    Dash -->|Queries Sessions| DB_Session
    Dash -->|Resumes Session\n(Approve/Reject)| AR
```
*(For a deep dive into the state machine and Node configurations, see [ARCHITECTURE.md](ARCHITECTURE.md))*

---

## 🚀 The Three Codelabs Journey

This project is the culmination of three core codelabs. For detailed notes and scraped learnings, see [docs/CODELABS.md](docs/CODELABS.md).

### 1️⃣ Building the Ambient ADK Graph Agent
* **Objective:** Build the core logic using ADK 2.0 graph workflow.
* **Achievements:**
  * Created deterministic routing (e.g., auto-approving <$100).
  * Integrated a **Security Checkpoint Node** to redact PII and short-circuit prompt injection attacks.
  * Used `agents-cli eval` to run local LLM-as-a-judge trace evaluations ensuring the agent acts safely and accurately.

### 2️⃣ Vibecoding the FastAPI Frontend & Pub/Sub
* **Objective:** Connect the agent to the real world using Pub/Sub and a Human-in-the-Loop (HITL) dashboard.
* **Achievements:**
  * Used natural language prompting in Antigravity to build a glassmorphism FastAPI dashboard.
  * Deployed the dashboard to Google Cloud Run.
  * Created an OIDC-authenticated Push subscription pointing directly to the Agent Runtime `:query` REST API.

### 3️⃣ Enterprise Cloud Scale Deployment
* **Objective:** Move from local development to production-grade deployment.
* **Achievements:**
  * Enabled Vertex AI and Agent Registry APIs.
  * Used the `agents-cli` for seamless CLI deployment.
  * Leveraged Cloud Trace, Cloud Logging, and BigQuery for comprehensive enterprise observability.

---

## 📂 Project Structure

```text
ambient_expense/
├── ambient-expense-agent/     # ADK 2.0 Graph Workflow Agent Backend
│   ├── expense_agent/         # Core agent nodes, schema, and logic
│   └── tests/                 # Local evaluations and datasets
├── submission_frontend/       # FastAPI Manager Dashboard
│   ├── main.py                # Dashboard logic and Vertex AI connection
│   ├── Dockerfile             # Container configuration for Cloud Run
│   └── static/                # Glassmorphism UI assets
├── docs/                      # Extensive Documentation
│   └── CODELABS.md            # Course learnings and takeaways
├── ARCHITECTURE.md            # Technical design and state machine diagrams
└── README.md                  # This file
```

---

## 💻 Getting Started

### Prerequisites
* [uv](https://docs.astral.sh/uv/getting-started/installation/index.md) (Python package manager)
* `google-agents-cli` installed globally (`uv tool install google-agents-cli`)
* Google Cloud SDK (`gcloud`) authenticated to your project.

### Running Locally

1. **Start the Agent Backend (Playground):**
   ```bash
   cd ambient-expense-agent
   make playground
   ```
2. **Start the Frontend Dashboard:**
   ```bash
   cd submission_frontend
   uv run uvicorn main:app --reload --port 8000
   ```
3. Open `http://localhost:8000` to view pending manager escalations!

---

<div align="center">
  <i>Built with ❤️ using Antigravity and the ADK 2.0 Framework during the Kaggle AI Agents Intensive.</i>
</div>
