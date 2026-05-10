# Shiksha-MFE Technical Audit Report
### Author: Yats (yats0x7) · C4GT 2026 Proposal Supplement
### Repository: https://github.com/tekdi/shiksha-mfe
### Audit Date: May 2026

---

## Executive Summary

The `shiksha-mfe` repository is a **frontend-only Nx monorepo** (Next.js 14 + React 18) that serves as the UI shell for the DMP 2026 AI content platform. The DMP ticket describes a 4-module full-stack AI system; however, **zero backend infrastructure exists in this repository**. Additionally, the frontend itself contains 3 critical architectural defects that will cause production failures independent of the missing backend.

This audit documents 7 specific issues, assigns severity ratings, and proposes concrete fixes.

---

## Audit Findings Summary

| # | Finding | Severity | Effort to Fix |
|---|---------|----------|---------------|
| 1 | Entire AI backend is missing | 🔴 Critical | 12 weeks |
| 2 | Fake MFE architecture (static imports, not Module Federation) | 🔴 Critical | 1 week |
| 3 | App Router ↔ Pages Router mismatch | 🔴 Critical | 2–3 days |
| 4 | No async queue — LLM calls will timeout | 🔴 Critical | Included in backend |
| 5 | Sunbird API env vars undocumented and fragile | 🟡 High | 1 day |
| 6 | No H5P specification validation layer | 🟡 High | Included in backend |
| 7 | Spelling errors in route folder names | 🟢 Low | 30 minutes |

---

## Finding 1 — Missing AI Backend (Critical)

### What's Missing
The DMP ticket specifies a 4-module AI platform:
- **Module A**: PDF/PPT ingestion → structured JSON (PyMuPDF + Ollama)
- **Module B**: MCQ/Quiz generation → H5P/SCORM packaging
- **Module C**: Whisper transcription → speaker diarisation + VTT
- **Module D**: Micro-lesson studio → HTML5/H5P/SCORM output

**None of this exists.** The repository contains only the Next.js consumer layer.

### What Exists (Frontend Only)
```
apps/
├── admin-app-repo/        ← Empty admin shell
├── learner-web-app/       ← Course consumption UI
└── teachers/              ← Teacher portal shell

mfes/
├── authentication/        ← Login/logout pages (Pages Router)
├── editors/               ← Sunbird collection editor wrappers
├── players/               ← SCORM/PDF/QUML player renderers
└── survey-observations/   ← Survey UI component
```

### Risk
Without the backend, every AI-powered action (upload PDF, generate quiz, transcribe video) has **no endpoint to call**. The frontend will show blank states or crash on empty API responses.

### Fix Required
Build a complete Python/FastAPI backend with:
- Async task queue (Celery + Redis) for LLM inference
- Ollama integration for local model serving
- Module A → D pipeline
- Docker Compose orchestration

See `api-gateway/` prototype in this repository for reference implementation.

---

## Finding 2 — Fake MFE Architecture (Critical)

### What Was Intended
The project is named "Micro Frontends" (MFE), implying **runtime composition via Webpack Module Federation** — where each MFE is independently deployed and loaded at runtime.

### What Actually Exists
The "MFEs" are statically compiled into host apps via **TypeScript path aliases**:

```json
// tsconfig.base.json (root)
{
  "compilerOptions": {
    "paths": {
      "@login": ["mfes/authentication/src/pages/login.tsx"],
      "@logout": ["mfes/authentication/src/pages/logout.tsx"],
      "@content": ["mfes/content/src/pages/index.tsx"]
    }
  }
}
```

This means at build time, webpack resolves `@login` directly to the source file. There is **no runtime boundary**. All "MFEs" are compiled into a single monolithic bundle.

### Why This Is Wrong
| Property | True MFE (Module Federation) | Current Implementation |
|----------|------------------------------|------------------------|
| Deployment | Each MFE deploys independently | All deploy together |
| Build | Each builds separately | Single monolithic build |
| Versioning | Independent versions | Shared version |
| Failure isolation | One MFE fails, others work | One failure crashes all |
| Bundle size | Lazy-loaded per MFE | Entire app bundled upfront |

The **only true Module Federation configuration** found is in `mfes/scp-teacher-repo/next.config.js`. The rest are static aliases.

### Fix
Implement `@module-federation/nextjs-mf` for each MFE:

```javascript
// mfes/authentication/next.config.js
const { NextFederationPlugin } = require('@module-federation/nextjs-mf');

module.exports = {
  webpack(config, options) {
    config.plugins.push(
      new NextFederationPlugin({
        name: 'authentication',
        filename: 'static/chunks/remoteEntry.js',
        exposes: {
          './Login': './src/components/Login',
          './Logout': './src/components/Logout',
        },
        shared: {
          react: { singleton: true, eager: true },
          'react-dom': { singleton: true, eager: true },
        },
      })
    );
    return config;
  },
};
```

---

## Finding 3 — App Router ↔ Pages Router Mismatch (Critical)

### The Problem
Next.js fundamentally isolates the App Router and Pages Router runtimes. They **cannot share components directly** without a compatibility wrapper.

**Host app** (`apps/learner-web-app`) uses App Router:
```
apps/learner-web-app/src/app/
├── layout.tsx       ← Root layout (App Router)
├── page.tsx         ← Home page (App Router)
└── dashboard/
    └── page.tsx     ← Dashboard (App Router)
```

**Authentication MFE** (`mfes/authentication`) uses Pages Router:
```
mfes/authentication/src/pages/
├── login.tsx        ← Login page (Pages Router)
└── logout.tsx       ← Logout page (Pages Router)
```

When the host app imports `@login` (resolved to Pages Router file) into an App Router layout, the following failures occur:
1. **Hydration mismatch errors** — SSR output doesn't match client render
2. **`useRouter()` incompatibility** — `next/router` (Pages) vs `next/navigation` (App Router)
3. **Middleware conflicts** — Pages Router middleware doesn't apply in App Router context

### Reproduction Steps
```bash
npx nx run learner-web-app:dev
# Navigate to /login
# Open browser DevTools → Console
# Expected: Hydration failed because the initial UI does not match
```

### Fix
Two options:

**Option A (Preferred):** Migrate `mfes/authentication` to App Router:
```
mfes/authentication/src/app/
├── login/
│   └── page.tsx     ← App Router login
└── logout/
    └── page.tsx     ← App Router logout
```

**Option B:** Wrap Pages Router components in a client boundary in App Router:
```tsx
// apps/learner-web-app/src/app/login/page.tsx
'use client';
import dynamic from 'next/dynamic';
const LoginPage = dynamic(
  () => import('@login'),
  { ssr: false }   // Disable SSR to avoid hydration mismatch
);
export default LoginPage;
```

---

## Finding 4 — No Async Queue (Critical, Backend Concern)

### Why This Matters
The DMP ticket specifies:
- Module A: ≤ 30 seconds for 50-page document
- Module D: ≤ 2 minutes end-to-end micro-lesson generation

Running **Llama 3 8B locally** for a 50-page document takes 45–120 seconds on CPU-only hardware. If this is implemented as a synchronous FastAPI endpoint:

```python
# ❌ WRONG — will timeout under load
@app.post("/api/ingest")
async def ingest_pdf(file: UploadFile):
    text = extract_text(file)           # 2–5 seconds
    summary = ollama.generate(text)     # 45–120 seconds ← TIMEOUT
    return summary
```

Default uvicorn request timeout is 60 seconds. Under concurrent requests, the single Ollama instance will queue internally, making response times unpredictable.

### Fix
All LLM inference **must** go through an async task queue:

```python
# ✅ CORRECT — async-first design
@app.post("/api/ingest")
async def ingest_pdf(file: UploadFile):
    task_id = str(uuid.uuid4())
    # Queue the heavy work — return immediately
    process_pdf.delay(task_id, file_path)
    return {"task_id": task_id, "status": "queued"}

# Frontend polls /api/tasks/{task_id} or subscribes to WebSocket
```

Full implementation in `api-gateway/tasks/ingestion.py`.

---

## Finding 5 — Undocumented Sunbird API Dependencies (High)

### Problem
The Next.js apps contain rewrites pointing to Sunbird infrastructure:

```javascript
// apps/learner-web-app/next.config.js (inferred from docker-compose)
env: {
  NEXT_PUBLIC_WORKSPACE_BASE_URL: process.env.NEXT_PUBLIC_WORKSPACE_BASE_URL,
  NEXT_PUBLIC_CLOUD_STORAGE_URL: process.env.NEXT_PUBLIC_CLOUD_STORAGE_URL,
}
```

There is **no `.env.example`** documenting required variables. A new developer cannot run this without access to `sunbird-editor.tekdinext.com` — a private Tekdi infrastructure endpoint.

### Fix
Add `.env.example` with all required variables:
```bash
# .env.example
NEXT_PUBLIC_WORKSPACE_BASE_URL=https://your-sunbird-instance.com
NEXT_PUBLIC_CLOUD_STORAGE_URL=https://your-storage.com
NEXT_PUBLIC_API_GATEWAY_URL=http://localhost:8000
OLLAMA_BASE_URL=http://localhost:11434
REDIS_URL=redis://localhost:6379
DATABASE_URL=postgresql://user:pass@localhost:5432/shiksha
```

---

## Finding 6 — No H5P Output Validation (High)

### Problem
The DMP ticket requires valid H5P packages importable into Moodle 4.x and Open edX. H5P has a strict JSON schema for `h5p.json` and `content/content.json`. If the LLM generates subtly malformed JSON (extra field, wrong key name, incorrect version string), **Sunbird editors will silently fail to load the content**.

This is the single highest-risk technical area in the entire project.

### H5P Package Structure (Required)
```
quiz-package.h5p   (ZIP file)
├── h5p.json              ← Library declaration (strict schema)
├── content/
│   └── content.json      ← Question content (type-specific schema)
└── H5P.QuestionSet-1.20/ ← Required library folder
    └── library.json
```

### Fix
Add a validation step in the packaging pipeline:
```python
# api-gateway/services/h5p_validator.py
import jsonschema
import json

H5P_SCHEMA = {
  "required": ["title", "mainLibrary", "language", "preloadedDependencies"],
  "properties": {
    "mainLibrary": {"type": "string", "pattern": "^H5P\\."},
    "preloadedDependencies": {
      "type": "array",
      "items": {
        "required": ["machineName", "majorVersion", "minorVersion"]
      }
    }
  }
}

def validate_h5p_manifest(h5p_json: dict) -> bool:
    jsonschema.validate(h5p_json, H5P_SCHEMA)
    return True
```

---

## Finding 7 — Spelling Errors in Route Names (Low)

### Problem
```
apps/learner-web-app/src/app/
├── attandence/          ← Should be: attendance/
└── profile-complition/  ← Should be: profile-completion/
```

These are URL routes. Current URLs will be:
- `/attandence` (broken, non-standard)
- `/profile-complition` (broken, non-standard)

This suggests **no PR review process** for the learner-web-app routes.

### Fix
```bash
git mv apps/learner-web-app/src/app/attandence \
        apps/learner-web-app/src/app/attendance

git mv apps/learner-web-app/src/app/profile-complition \
        apps/learner-web-app/src/app/profile-completion
```

Update any internal links referencing these routes.

---

## Architecture Recommendation

```
┌─────────────────────────────────────────────────────────┐
│                   Shiksha Platform                       │
│                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │ Learner App │  │ Teacher App │  │  Admin App  │     │
│  │  (Port 3003)│  │  (Port 3001)│  │  (Port 3002)│     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         └────────────────┼────────────────┘             │
│                          │ REST / WebSocket              │
│                          ▼                               │
│              ┌───────────────────────┐                  │
│              │   FastAPI Gateway     │                  │
│              │      Port 8000        │                  │
│              └─────────┬─────────────┘                  │
│                        │                                 │
│          ┌─────────────┼─────────────┐                  │
│          ▼             ▼             ▼                   │
│      ┌───────┐    ┌────────┐   ┌──────────┐            │
│      │ Redis │    │ Ollama │   │ Postgres │            │
│      │ Queue │    │ :11434 │   │  :5432   │            │
│      └───┬───┘    └────────┘   └──────────┘            │
│          │                                               │
│    ┌─────▼──────┐                                       │
│    │   Celery   │                                       │
│    │   Workers  │                                       │
│    │ Module A-D │                                       │
│    └────────────┘                                       │
└─────────────────────────────────────────────────────────┘
```

---

## Conclusion

The `shiksha-mfe` repository is a well-structured frontend shell that needs a complete backend to function. The most critical path item is **building the async AI pipeline (Modules A–D)** as a decoupled FastAPI service. Simultaneously, the frontend has 3 architectural defects (Findings 2, 3, 4) that must be addressed before the backend integration can be stable.

This audit was conducted by reviewing the repository structure, configuration files, and dependency manifests. All findings are reproducible and documented with exact file references.

---

*Prepared as part of C4GT 2026 proposal for Tekdi / Shiksha-MFE*
*GitHub: biru-codeastromer / yats0x7*