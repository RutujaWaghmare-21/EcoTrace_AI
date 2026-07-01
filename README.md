# 🌱 EcoTrace AI

## Smart Supply Chain & Carbon Footprint Auditor using Multi-Agent AI

EcoTrace AI is a multi-agent AI platform that helps businesses analyze supply chains, estimate carbon emissions, identify sustainability risks, optimize logistics decisions, and simulate environmental improvements.

Built for the **Kaggle AI Agents: Intensive Vibe Coding Capstone Project with Google**, EcoTrace AI demonstrates how modern AI agents can move beyond chat interfaces and become intelligent decision-support systems for real-world business challenges.

---

## 🚀 Features

### 📄 Document Analysis

Upload supply chain documents including:

- **PDF** reports
- Supplier spreadsheets
- Shipping manifests
- **CSV** files
- **XLSX** files

The system automatically extracts suppliers, transport methods, locations, and logistics information.

---

### 🌍 Carbon Footprint Estimation

EcoTrace AI estimates emissions based on:

- Transport methods
- Shipment distances
- Supplier locations
- Product categories
- Logistics routes

---

### ⚠️ Supplier Sustainability Risk Analysis

The platform evaluates suppliers using:

- Estimated emissions
- Transport intensity
- Geographic distance
- Sustainability indicators
- Environmental disclosures

---

### 🎯 Goal Optimization Agent

Users can define sustainability goals such as:

- Reduce emissions by 30%
- Limit cost increases to 5%
- Reduce air freight dependency
- Prioritize local sourcing

The agent generates an optimized action plan to achieve these objectives.

---

### 🔬 Scenario Simulation Agent

Perform *what-if* analysis instantly.

Examples:

- What if we replace air freight with sea freight?
- What if we switch suppliers?
- What if we consolidate shipments?
- What if we source locally?

The platform recalculates projected emissions and impacts automatically.

---

### 📊 Executive Reporting

Generate reports containing:

- Carbon hotspots
- Supplier rankings
- Optimization opportunities
- Sustainability recommendations
- Executive summaries

---

## 🧠 AI Agent Architecture

EcoTrace AI uses a specialized multi-agent architecture:

### Planner Agent

Coordinates workflows and delegates tasks to specialized agents.

### Document Extraction Agent

Processes uploaded files and converts them into structured data.

### Carbon Estimation Agent

Calculates estimated emissions and identifies major contributors.

### Supplier Risk Agent

Evaluates suppliers and generates sustainability scores.

### Optimization Goal Agent

Creates emission reduction strategies under business constraints.

### Scenario Simulation Agent

Runs hypothetical supply chain simulations.

### Report Generation Agent

Produces executive-ready reports and recommendations.

---

## 🛠️ Technology Stack

### Backend

- Python

### AI Models

- Gemini **API**

### Frontend

- Streamlit

### Vector Database

- **FAISS**

### Libraries

- Pandas
- Plotly
- PyPDF2
- Pydantic
- LangChain

---

# Project Structure

<img width="608" height="747" alt="image" src="https://github.com/user-attachments/assets/60204078-b475-4155-b4c4-442b2dfb860d" />

## 📷 Application Workflow

*1. Upload supply chain documents. *2. Extract suppliers and logistics information. *3. Estimate emissions and identify carbon hotspots. *4. Analyze supplier sustainability risks. *5. Generate optimization strategies. *6. Simulate alternative scenarios. *7. Produce sustainability reports.

---

## 💡 Example Use Cases

### Supply Chain Audit

Identify suppliers and routes responsible for the highest emissions.

### Sustainability Planning

Generate actionable plans to reduce emissions.

### Scenario Analysis

Evaluate potential operational changes before implementation.

### ESG Reporting

Generate reports for stakeholders and decision makers.

---

## 📈 Example Optimization Result

| Metric                  | Current    | Optimized |
| ----------------------- | ---------- | --------- |
| Total Emissions         | 1240 tCO₂e | 870 tCO₂e |
| Sustainability Score    | 72         | 88        |
| Reduction Potential     | -          | 30.4%     |
| Estimated Cost Increase | -          | 4.2%      |

---

## ▶️ Installation

Clone the repository:

Install dependencies:

```bash pip install -r requirements.txt ```

Create a `.env` file:

```env GOOGLE_API_KEY=your_gemini_api_key ```

Run the application:

```bash streamlit run app.py ```

---

## 🌍 Why EcoTrace AI?

Sustainability consulting and carbon auditing are often expensive and inaccessible for smaller organizations.

EcoTrace AI democratizes sustainability intelligence by combining AI agents, retrieval, reasoning, optimization, and simulation into a single platform that helps businesses make smarter environmental decisions.

---

## 🔮 Future Improvements

- Real-time logistics integrations
- Live shipment tracking
- Industry-specific emission models
- Geographic route optimization
- **ESG** compliance templates
- Cost prediction models

## ---

## 📜 License

This project is released under the **MIT** License.

---
