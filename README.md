# Personal Accountant - Production-Grade Bill Management System

A boringly reliable, transparent, and safe personal accounting assistant designed for non-technical users (specifically Indian parents managing household bills).

## 🎯 Design Philosophy

**Correctness > Cleverness**

This system is built with the assumption that:
- Images will be blurry phone photos
- Bills will be imperfect
- Users will make mistakes
- Users have zero technical literacy
- This will be used daily for real financial tracking

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        STREAMLIT UI                              │
│                  (Human-in-the-loop interface)                   │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATION LAYER                         │
│              (Pydantic AI with strict boundaries)                │
│                                                                  │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│   │ Bill Upload │  │ Query Agent │  │ Validation Pipeline    │ │
│   │    Agent    │  │ (RAG-based) │  │ (Schema + Semantic)    │ │
│   └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Cloudinary   │     │     Mindee      │     │  Google Sheets  │
│   (Image      │     │  (Structured    │     │   (Storage +    │
│  Enhancement) │     │     OCR)        │     │   Audit Log)    │
└───────────────┘     └─────────────────┘     └─────────────────┘
```

## 🔒 Core Principles

1. **AI suggests → Human confirms → System verifies**
2. **Fail early, fail visibly**
3. **No silent corrections**
4. **Every step must be auditable**
5. **Storage layer is swappable**

## 📁 Project Structure

```
personal-accountant/
├── src/
│   ├── models/           # Pydantic data models (strict schemas)
│   ├── services/         # External service integrations
│   │   ├── image/        # Cloudinary enhancement
│   │   ├── ocr/          # Mindee structured OCR
│   │   └── storage/      # Google Sheets (abstract interface)
│   ├── agents/           # Pydantic AI agents
│   ├── validation/       # Two-stage validation pipeline
│   ├── queries/          # Structured query execution (RAG)
│   └── audit/            # Audit logging
├── app/                  # Streamlit application
├── config/               # Configuration management
├── tests/                # Test suite
└── docs/                 # Documentation
```

## 🚀 User Journeys

### 1️⃣ Adding a Bill

1. User uploads photo
2. **Image Enhancement** (Cloudinary) → Quality check
3. **Structured OCR** (Mindee) → Document type validation
4. **Two-Stage Validation** → Schema + Semantic checks
5. **Human Confirmation** → Explicit approval required
6. **Persistence** → Save to Google Sheets + Audit log

### 2️⃣ Asking Questions (Agentic RAG)

1. User asks natural language question
2. **LLM converts to structured query** (NO direct answering)
3. **Query executes on stored data** (deterministic)
4. **LLM generates response from actual data**
5. **No data = explicit "no data" response** (NO hallucination)

## ⚙️ Tech Stack

- **Frontend**: Streamlit (Python)
- **Image Enhancement**: Cloudinary
- **OCR**: Mindee (financial documents)
- **AI Orchestration**: Pydantic AI
- **Storage**: Google Sheets (swappable)
- **LLM**: Gemini 1.5 Flash

## 🛡️ What This System Will NEVER Do

- ❌ Let LLM answer questions directly without data lookup
- ❌ Store unverified OCR results
- ❌ Make silent corrections
- ❌ Skip human confirmation
- ❌ Hallucinate financial data

## 📋 Setup

1. Clone the repository
2. Copy `.env.example` to `.env` and fill in credentials
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `streamlit run app/main.py`

## 🔑 Required Environment Variables

- `CLOUDINARY_CLOUD_NAME`
- `CLOUDINARY_API_KEY`
- `CLOUDINARY_API_SECRET`
- `MINDEE_API_KEY`
- `GOOGLE_SHEETS_CREDENTIALS_PATH`
- `GOOGLE_SHEETS_SPREADSHEET_ID`
- `GEMINI_API_KEY`

---

Built with ❤️ for real people managing real money.
