# Economic Warfare OSINT System

A multi-agent OSINT system that takes natural language questions about sanctions, supply chain disruption, and investment interception, then autonomously gathers data from free public sources and produces structured impact assessments with entity graphs.

Built on a three-layer architecture: a Claude-powered **Orchestrator** decomposes questions into research plans, **MCP Tool Agents** query 15+ free data sources in parallel, and a **Fusion Engine** renders reports with interactive vis.js network graphs.

## Quick Start

**Prerequisites:** Python 3.12+, [uv](https://docs.astral.sh/uv/)

```bash
# Clone and install
git clone https://github.com/BaileyM7/economic-warfare-osint.git
cd economic-warfare-osint
uv sync

# Configure API keys
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY (required)
# All other keys are free-tier — see .env.example for registration links

# Run the web server
uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser and ask a question.

## API Keys

| Key | Required | Free? | Source |
|-----|----------|-------|--------|
| `ANTHROPIC_API_KEY` | **Yes** | Pay-per-use (~$0.03-0.20/query) | [console.anthropic.com](https://console.anthropic.com/) |
| `FRED_API_KEY` | No | Yes | [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html) |
| `COMTRADE_API_KEY` | No | Yes (500 calls/day) | [comtradeplus.un.org](https://comtradeplus.un.org/) |
| `ACLED_API_KEY` | No | Yes (registration) | [developer.acleddata.com](https://developer.acleddata.com/) |
| `OPENCORPORATES_API_KEY` | No | Yes (rate-limited without key) | [opencorporates.com](https://opencorporates.com/) |

Sources that need no key: OFAC SDN, OpenSanctions, GLEIF, ICIJ Offshore Leaks, GDELT, IMF, World Bank, yfinance, SEC EDGAR.

## CLI Mode

```bash
uv run python -m src.orchestrator.main "What happens if we sanction Fujian Jinhua?"
```

## Wargame Tab

The `/wargame` tab (geopolitical agent simulation) requires a separately
deployed **swarm backend** microservice. The ported frontend lives in
[frontend/src/wargame/](frontend/src/wargame/), but the sim engine, agent
memory, and WebSocket event stream all run out-of-process.

- **Deployment guide:** [docs/wargame-deployment.md](docs/wargame-deployment.md)
- **Smoke test script:** `scripts/check-swarm-connection.sh <swarm-url> <emissary-origin>`
- **Env vars on the Emissary frontend build:** `VITE_SWARM_API_URL`, `VITE_SWARM_WS_URL`

Without swarm deployed, the tab still loads and the **PLAY DEMO (SCRIPTED)**
button runs a client-side scripted Taiwan 2027 scenario. Only the preset
**Execute Simulation** and freeform **Analyze** actions require swarm.

## Data Sources

- **Sanctions:** OpenSanctions.org, OFAC SDN
- **Corporate:** OpenCorporates, GLEIF (LEI registry), ICIJ Offshore Leaks
- **Market:** Yahoo Finance, SEC EDGAR 13F, FRED
- **Trade:** UN Comtrade, UNCTADstat
- **Geopolitical:** GDELT 2.0, ACLED
- **Economic:** FRED, IMF APIs, World Bank
