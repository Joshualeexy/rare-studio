# 🤖 Headless Web Intelligence Microservice (`services/scraper`)

A standalone, asynchronous Node.js browser automation microservice powering real-time web intelligence for MoneyPrinter Studio. Built with Playwright Stealth, hardware fingerprint synthesis, and automated client-side challenge resolution.

---

## Architecture & Features
- **Anti-Fingerprinting Browser Automation**: Normalizes WebGL parameters, Chrome DevTools Protocol (CDP) artifacts, and canvas dimensions via Playwright Stealth integration.
- **Hardware Profile Spoofing**: Synthesizes realistic display metrics, audio contexts, and hardware concurrency values to prevent synthetic traffic classification.
- **Automated Challenge Resolution**: Integrates pre-packaged CapSolver extension hooks and session recovery for automated checkpoint clearance.
- **Persistent Session Pool**: Maintains an active Chromium instance across incoming pipeline requests, avoiding cold browser launch latencies.
- **HTTP REST API**: Exposes JSON endpoints for live health checks and structured search result extraction.

---

## API Endpoints

### 1. Health Check
`GET /health`

**Response:**
```json
{
  "status": "ok",
  "browserReady": true,
  "port": 4050
}
```

### 2. Search Intelligence
`GET /api/search?q=:query&limit=:limit`

**Parameters:**
- `q` (string, required): The search query or investigative topic.
- `limit` (number, optional, default: 5): Maximum number of organic search results to return.

**Response:**
```json
{
  "query": "The Dark Psychology of Cult Indoctrination",
  "answer_box": "Cult indoctrination relies heavily on deceptive recruitment, love bombing, and progressive isolation...",
  "results": [
    {
      "title": "Understanding Cult Manipulation Tactics",
      "snippet": "Coercive persuasion techniques alter individual autonomy through progressive isolation...",
      "url": "https://..."
    }
  ],
  "people_also_ask": [
    "What are the stages of cult indoctrination?",
    "How does cognitive dissonance play a role in cult loyalty?"
  ]
}
```

---

## Configuration

Create or update `.env` in this directory:
```env
CAPSOLVER_API_KEY=your_capsolver_key
SCRAPER_PORT=4050
```

---

## Standalone Execution

To run the microservice independently:
```bash
npm start
```
Or with auto-restart during development:
```bash
node server.js
```

Or query via the command-line helper:
```bash
node search_cli.js "Apollo 11 missing telemetry tapes"
```
