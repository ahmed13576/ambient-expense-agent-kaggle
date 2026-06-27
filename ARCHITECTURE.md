# System Architecture & Technical Deep-Dive

This document outlines the system architecture for the **Ambient Expense Agent**, showcasing the event-driven Google Cloud infrastructure and the ADK 2.0 graph workflow logic that powers the autonomous and human-in-the-loop (HITL) processes.

---

## High-Level Cloud Architecture

The application operates as an **event-driven ambient agent**. Instead of waiting for a user to type into a chat window, it listens for system events (like a new expense report being ingested from an ERP system) and triggers autonomously.

```mermaid
flowchart TD
    %% Define Styles
    classDef gcp fill:#4285F4,color:white,stroke:#fff,stroke-width:2px;
    classDef agent fill:#0F9D58,color:white,stroke:#fff,stroke-width:2px;
    classDef dashboard fill:#F4B400,color:white,stroke:#fff,stroke-width:2px;
    classDef external fill:#DB4437,color:white,stroke:#fff,stroke-width:2px;

    ERP["Upstream System\n(e.g., ERP/Email)"]:::external -->|Publishes Event| PS["Google Cloud Pub/Sub\n(Topic: expense-reports)"]:::gcp
    
    PS -->|OIDC Push Subscription| AR["Vertex AI Agent Runtime\n(Stateful Engine)"]:::agent
    
    AR -->|Auto-Approve| DB_Store[("Backend System\n(Approved)")]:::external
    
    AR -.->|Requires Manager Escalation\n(State: Paused)| DB_Session[("Agent Runtime\nSession Store")]:::gcp
    
    Manager["Human Manager"]:::external -->|Reviews| Dash["FastAPI Dashboard\n(Cloud Run)"]:::dashboard
    Dash -->|Queries Sessions| DB_Session
    Dash -->|Resumes Session\n(Approve/Reject)| AR
    
    AR -.->|Dead-Letter Topic\n(If 5x Failures)| DLT["Pub/Sub DLT\n(expense-reports-dead-letter)"]:::gcp
```

### Key Components

1. **Google Cloud Pub/Sub**: Acts as the ingestion layer. Upstream systems publish expense payloads here. A push subscription directly invokes the Agent Runtime via OIDC authentication, ensuring serverless and scalable event routing.
2. **Vertex AI Agent Runtime**: Hosts the compiled ADK 2.0 agent. It manages the stateful execution of the graph workflow. If the workflow hits a `RequestInput` node, the runtime automatically pauses and serializes the state to the session store.
3. **Cloud Run Dashboard**: A FastAPI-based manager dashboard deployed to Cloud Run. It utilizes `roles/aiplatform.user` IAM permissions to query the Vertex AI session store for pending manager escalations and submit the human decision to resume the agent's workflow.

---

## ADK 2.0 Graph Workflow State Machine

The core intelligence of the agent is modeled as an ADK 2.0 Workflow graph. It combines deterministic code (for strict business logic and security) with generative AI (for qualitative risk assessment).

```mermaid
stateDiagram-v2
    direction TB
    
    [*] --> ParseEvent : Incoming Payload
    ParseEvent --> CheckThreshold : Extract Expense Details
    
    state CheckThreshold {
        direction LR
        IsAmount {
            < $100
            >= $100
        }
    }
    
    CheckThreshold --> AutoApprove : Amount < $100
    CheckThreshold --> SecurityCheckpoint : Amount >= $100
    
    state SecurityCheckpoint {
        direction LR
        ScrubPII
        DetectInjection
    }
    
    SecurityCheckpoint --> HumanApproval : Injection Detected! (Bypass LLM)
    SecurityCheckpoint --> LLMRiskReview : Clean (Proceed)
    
    LLMRiskReview --> HumanApproval : Provide Risk Analysis
    
    HumanApproval --> [*] : Paused (Awaiting Human Input via Dashboard)
    
    AutoApprove --> [*] : Instantly Approved
```

### Node Explanations

- **`ParseEvent`**: A deterministic function node that catches the raw Pub/Sub payload, base64-decodes it, and unpacks the JSON into a strongly typed `Expense` Pydantic model.
- **`CheckThreshold`**: A routing node containing strict business logic. It checks if the expense is under $100. If so, it takes the deterministic fast-path to `AutoApprove`.
- **`SecurityCheckpoint`**: A critical enterprise security node. It scrubs PII (like Social Security Numbers) and scans for prompt injection attacks. If an attack is detected, it short-circuits the LLM and sends the request directly to the human manager.
- **`LLMRiskReview`**: Calls the Gemini model to perform qualitative analysis on the expense description and category (e.g., "Why is someone buying a surfboard under 'office supplies'?").
- **`HumanApproval`**: A stateful node that yields a `RequestInput` interrupt. The Agent Runtime pauses execution here, saving the context and LLM analysis until a human manager interacts via the frontend dashboard.
