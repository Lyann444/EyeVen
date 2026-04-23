# EyeVen — AI-Powered Vendor Onboarding & Compliance Orchestration

SunAI EyeVen transforms messy, unstructured vendor submission packages into structured, verifiable, risk-aware, reviewable onboarding cases for procurement teams.

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- MySQL 8.0+
- Docker (optional)

### Backend
```bash
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
flask db upgrade
python run.py
```

### Frontend
```bash
cd frontend
npm install
npm start
```

### Docker
```bash
docker-compose up --build
```

## Tech Stack
- **Frontend:** React, TypeScript, Material UI, Chart.js
- **Backend:** Python, Flask
- **OCR:** PaddleOCR
- **Database:** MySQL
- **Reasoning:** Z AI GLM
- **Deployment:** Vercel (frontend), Docker (backend)
