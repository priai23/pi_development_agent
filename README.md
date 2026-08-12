# PI ERP Implementation Agent

An advanced, agentic AI assistant designed explicitly for **Odoo 19 Custom Module Development**, built by Primacy Infotech. 

The application uses a **LangGraph-powered** reactive agent capable of reasoning, planning, and securely writing Python and XML directly to your local file system to build Odoo modules from scratch. The agent operates with a **Human-in-the-loop (HITL)** architecture, ensuring that all potentially unsafe filesystem operations are presented for your explicit approval before execution.

---

## 🌟 Features

- **Agentic Odoo 19 Developer:** Integrated with an extensive Knowledge Base (`skills/Odoo19_Dev_Customization_KB.md`) tailored to the latest Odoo 19 ORM, View, and Security paradigms.
- **Human-in-the-loop (HITL):** Safe and transparent execution. When the agent wants to write or modify a file, you get an actionable "Action Pending" card in the UI to approve or reject the change.
- **Dynamic Model Selection:** Configurable OpenRouter integration. Fetch, search, and switch between 400+ LLMs directly from the polished Settings UI.
- **Apple-Style Aesthetics:** A buttery smooth, premium interface built with Next.js, Tailwind CSS, and Framer Motion. 
- **Local Postgres Database:** Securely stores your OpenRouter API keys, model preferences, and workspaces.

---

## 🏗️ Architecture

The project is structured as a full-stack application:

- **`/backend`**: 
  - **FastAPI** powering the REST endpoints.
  - **LangGraph** defining the stateful reactive agent (`agent.py`).
  - **SQLAlchemy** + **PostgreSQL** for data persistence.
  
- **`/frontend`**:
  - **Next.js 15** (App Router) with React Server Components.
  - **Tailwind CSS** + **Framer Motion** for a premium, micro-animation-heavy UI.
  - Dynamic OpenRouter configuration and real-time Server-Sent Events (SSE) chat streaming.

- **`/skills`**:
  - Custom markdown-based instructions and knowledge bases injected directly into the agent's context.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Node.js 18+
- PostgreSQL (running and accessible)
- OpenRouter API Key

### 1. Setup Backend

1. Navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Configure your environment variables:
   - Ensure you have a `.env` file or export your `DATABASE_URL` (e.g., `postgresql://postgres:postgres@localhost:5432/erp_agent`).
5. Start the FastAPI server:
   ```bash
   uvicorn main:app --reload --port 8001
   ```

### 2. Setup Frontend

1. Navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Install dependencies:
   ```bash
   npm install
   ```
3. Start the Next.js development server:
   ```bash
   npm run dev
   ```
4. Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## ⚙️ Configuration

Before chatting with the agent, you must configure your OpenRouter API key:

1. Open the application at [http://localhost:3000](http://localhost:3000).
2. Navigate to **Settings** in the sidebar.
3. Paste your **OpenRouter API Key**.
4. Use the searchable combobox to select your preferred **LLM Model** (e.g., `openai/gpt-4o`, `anthropic/claude-3.5-sonnet`).
5. Click **Save Settings**.

---

## 🛠️ Usage

1. **Create a Workspace**: Navigate to the home page or projects page to create a new module building workspace.
2. **Chat with the Agent**: Prompt the agent to build an Odoo 19 module (e.g., *"Create a Real Estate Property Management module"*).
3. **Approve Actions**: The agent will plan the module architecture. When it attempts to scaffold directories or write `__manifest__.py`, an "Action Pending" card will appear. Review the code and click **Approve** to execute the filesystem write.

---

## 📄 License
Internal use only - Primacy Infotech.
