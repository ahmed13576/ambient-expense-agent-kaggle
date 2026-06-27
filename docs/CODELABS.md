# 5-Day AI Agents Intensive: Codelabs Summary

This repository encapsulates the knowledge and implementations from the **5-Day AI Agents Intensive Vibecoding Course with Google**. The course was divided into three main hands-on codelabs, guiding us from building a local ambient agent to a fully deployed enterprise-scale architecture.

Below is a detailed summary of the concepts, architectures, and key takeaways from each codelab.

---

## 1. Vibecode an ADK 2.0 Ambient Agent with Antigravity and Agents CLI

**Objective:** Build the core logic of the ambient expense agent using the Agent Development Kit (ADK) 2.0 graph workflow.

### Key Concepts Taught
- **ADK 2.0 Graph Workflow:** We utilized code-based routing logic (function nodes and edges) where deterministic rules handle low-risk cases (e.g., expenses under $100) and Large Language Models (LLMs) handle ambiguous or high-risk cases.
- **Ambient Agents:** Rather than building a conversational chat interface, we designed an agent that runs in the background. It is triggered by system events (like Pub/Sub payloads) and operates autonomously.
- **Enterprise Security:** We implemented a pre-LLM security checkpoint node. This node acts as a shield to redact Personally Identifiable Information (PII) like SSNs and short-circuits prompt injection attacks, routing them directly to human review for safety.
- **Local Evaluation:** Using `agents-cli eval`, we set up synthetic datasets and automated trace generation to evaluate routing correctness and security containment using custom LLM-as-a-judge metrics.

### Key Code Snippets
```bash
# Install ADK tools and start the local playground
uvx google-agents-cli setup
make playground

# Triggering the ambient endpoint locally (simulating Pub/Sub)
curl -s http://localhost:8080/apps/expense_agent/trigger/pubsub \
  -H "Content-Type: application/json" \
  -d "{\"message\":{\"data\":\"$(printf '%s' '{\"amount\":45,\"submitter\":\"bob@company.com\",\"category\":\"meals\",\"description\":\"Team lunch\",\"date\":\"2026-04-12\"}' | base64)\",\"attributes\":{\"source\":\"test\"}},\"subscription\":\"test-sub\"}"

# Running the automated LLM-as-judge evaluations
make generate-traces && make grade
```

---

## 2. Vibecode and Deploy a Frontend for an ADK Agent

**Objective:** Build a Human-in-the-Loop (HITL) dashboard and wire it up to an event-driven Pub/Sub architecture.

### Key Concepts Taught
- **Event-Driven Architecture:** We utilized Google Cloud Pub/Sub push subscriptions to route event payloads directly to the Agent Runtime without needing an intermediary compute layer like Cloud Functions.
- **Vibecoding:** We guided the Antigravity agentic IDE through natural language to rapidly build a FastAPI frontend featuring a modern glassmorphism UI.
- **Cloud Run Deployment:** The FastAPI dashboard was containerized and deployed to Cloud Run, a scalable, serverless environment. We configured the necessary IAM permissions (`roles/aiplatform.user`) for it to interact securely with Vertex AI.
- **Session Management:** We used the ADK `VertexAiSessionService` to query for paused sessions—specifically those where the agent hit an `adk_request_input` step requiring manager escalation—and resume them based on human input from the dashboard.

### Key Code Snippets
```bash
# Creating Pub/Sub topics
gcloud pubsub topics create expense-reports
gcloud pubsub topics create expense-reports-dead-letter

# Simulating an event payload published to Pub/Sub
gcloud pubsub topics publish expense-reports \
  --message='{"input": {"message": "{\"amount\": 250, \"submitter\": \"alice@company.com\", \"category\": \"travel\", \"description\": \"NYC Flight Tickets\", \"date\": \"2026-04-12\"}"}}'
```

---

## 3. Enterprise Cloud Scale: Deploying the Expense Agent to Agent Runtime on Google Cloud

**Objective:** Take the ADK 2.0 agent from local development to a production-grade deployment on Google Cloud's Agent Runtime using `agents-cli`.

### Key Concepts Taught
- **Cloud Setup:** We provisioned a Google Cloud project and enabled the necessary generative APIs (`aiplatform`, `cloudtrace`, `cloudbuild`, `agentregistry`).
- **Antigravity Tooling:** We leveraged the `agents-cli` toolchain within the Antigravity IDE for project scaffolding, validation (dry-runs), and seamless deployments.
- **Agent Runtime:** We learned the architectural benefits of Vertex AI Agent Runtime, including its ability to manage stateful execution (crucial for our HITL pause/resume logic), secure sandboxing, and out-of-the-box enterprise observability.
- **Observability:** We utilized Cloud Trace and Cloud Logging to monitor agent execution paths and set up telemetry analytics via BigQuery.
- **Enterprise Discovery:** The agent was automatically registered in the Gemini Enterprise Agent Registry, making it discoverable across the organization.

### Key Code Snippets
```bash
# Set up agents-cli and skills in Antigravity
uvx google-agents-cli setup
agents-cli info

# Scaffold the initial agent and production files
agents-cli scaffold create expense-agent --adk
agents-cli scaffold enhance --deployment-target agent_runtime --yes

# Dry-run and deploy
uv lock
agents-cli deploy --dry-run
agents-cli deploy --project YOUR_PROJECT_ID --region us-west1

# Clean up Artifact Registry images
gcloud artifacts docker images delete
```
