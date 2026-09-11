# 🧠 NeuroGames — Full-Stack Application

Standalone ADHD-friendly cognitive games platform built with Next.js and FastAPI.

> **Sandbox mode**: This app is independent from the main MLOps pipeline. Designed for experimentation and validation before integration.

## Quick Start

### 1. Frontend (Next.js — port 3001)

```bash
cd neurogames-app/frontend

# Install deps (first time only)
npm install

# Start dev server
npm run dev -- -p 3001
```

Open **http://localhost:3001** to play.

### 2. Backend (FastAPI — port 8000) *(optional)*

The frontend works offline with localStorage. Connect the backend for session persistence and AI reports.

```bash
cd neurogames-app/backend

# Activate your venv (from project root)
# Windows:  ..\..\venv\Scripts\activate

pip install -r requirements.txt
uvicorn app.main:app --port 8000 --reload
```

- **API Docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health

### 3. Run Tests

```bash
cd neurogames-app/backend
pytest tests/ -v
```

## Architecture

```
neurogames-app/
├── backend/                        # FastAPI (port 8000)
│   ├── app/
│   │   ├── main.py                 # App entry + CORS + routers
│   │   ├── config.py               # Env-based settings
│   │   ├── routers/                # health, games, participants
│   │   ├── services/               # Business logic
│   │   └── models/                 # Pydantic schemas
│   └── tests/                      # Pytest
│
├── frontend/                       # Next.js 14 (port 3001)
│   └── src/
│       ├── app/                    # Pages
│       │   ├── page.tsx            # Login (avatar + name)
│       │   ├── games/              # Game carousel + game canvas
│       │   ├── history/            # Session history timeline
│       │   ├── profile/            # Player stats + badges
│       │   └── rapport/            # AI clinical report
│       ├── components/
│       │   ├── Sidebar.tsx         # Left navigation sidebar
│       │   ├── GameCanvas.tsx      # Game engine bridge + HUD + results
│       │   └── GameCanvas.module.css
│       ├── engine/                 # Ported game engines (5 games)
│       │   ├── GoNoGoGame.ts
│       │   ├── MemoryGame.ts
│       │   ├── TrackingGame.ts
│       │   ├── ShapesGame.ts
│       │   └── PuzzleGame.ts
│       └── lib/
│           ├── api.ts              # Backend API client (NeuroAPI)
│           ├── history.ts          # HistoryManager (localStorage)
│           └── gamification.ts     # Streak, XP, badges
│
└── README.md
```

## Pages

| Route | Description |
|-------|-------------|
| `/` | Login — avatar picker + name input |
| `/games` | Game carousel menu (5 games) |
| `/games/[id]` | Game canvas with HUD, instructions, results |
| `/history` | Session timeline with streak widget + filters |
| `/profile` | Player stats, per-game progress, badges |
| `/rapport` | AI-generated clinical report (Mistral LLM) |

## Key Features

- **ADHD-Friendly UI**: Minimal distractions, big buttons, clear instructions
- **Pre-game Instructions**: Shown before each game, re-viewable via `?` tooltip
- **Sidebar Navigation**: Profile, Games, History, Rapport sections
- **Rich Results Screen**: Trophy, stars, encouragement banners, metrics
- **Session History**: localStorage persistence, grouped by day, trend badges
- **Gamification**: XP system, streak tracking, 10 earnable badges
- **Clinical Report**: AI-powered cognitive analysis via backend API

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NG_PORT` | `8000` | Backend port |
| `NG_CORS_ORIGINS` | `http://localhost:3001` | Allowed CORS origins |

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Next.js 14, React 19, TypeScript, CSS Modules |
| Backend | FastAPI, Pydantic v2, Uvicorn |
| State | localStorage (HistoryManager + player profile) |
| Games | HTML5 Canvas API (5 cognitive games) |
| Testing | Pytest, FastAPI TestClient |
