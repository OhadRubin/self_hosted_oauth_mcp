# Self-Hosted OAuth MCP Server

A self-hosted MCP (Model Context Protocol) server with OAuth authentication using Keycloak as the identity provider. This setup allows you to run an OAuth-protected MCP server entirely on your own infrastructure.

## Architecture

```
┌─────────────┐      ┌─────────────────┐      ┌──────────────────┐
│   Client    │─────▶│  nginx (:9000)  │─────▶│  mcp-server      │
│             │      │                 │      │  (:8007)         │
└─────────────┘      │  /mcp ──────────┼─────▶│  FastMCP + JWT   │
                     │                 │      └──────────────────┘
                     │  /realms/* ─────┼─────▶┌──────────────────┐
                     │  /authorize     │      │  Keycloak        │
                     │  /token         │      │  (:9090)         │
                     └─────────────────┘      └──────────────────┘
```

- **nginx**: Reverse proxy on port 9000, routes MCP traffic and OAuth endpoints
- **Keycloak**: OAuth 2.0 / OpenID Connect identity provider
- **mcp-server**: FastMCP server with JWT token verification

## Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development)
- [uv](https://github.com/astral-sh/uv) (Python package manager)

## Quick Start

1. **Start the services**:
   ```bash
   docker compose up -d
   ```

2. **Wait for initialization** (takes ~30 seconds):
   ```bash
   docker compose logs -f keycloak-init
   ```
   The init service automatically configures Keycloak and writes credentials to `.env`.

3. **Test the MCP client**:
   ```bash
   uv run python client.py
   ```

## Configuration

### Keycloak Settings

| Setting | Value |
|---------|-------|
| Realm | `mcp` |
| Client ID | `mcp-server` |
| Default User | `user` / `password` |
| Admin Console | http://localhost:9090 (`admin` / `admin`) |

### Environment Variables

The `keycloak-init` service creates a `.env` file with:

| Variable | Description |
|----------|-------------|
| `KEYCLOAK_CLIENT_ID` | OAuth client ID |
| `KEYCLOAK_CLIENT_SECRET` | OAuth client secret |
| `MCP_TOKEN` | Pre-generated JWT access token |

### MCP Server Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `KEYCLOAK_URL` | `http://localhost:9090` | Internal Keycloak URL for JWKS |
| `KEYCLOAK_ISSUER_URL` | `http://localhost` | Expected JWT issuer |
| `KEYCLOAK_REALM` | `mcp` | Keycloak realm name |
| `KEYCLOAK_AUDIENCE` | `account` | Expected JWT audience |

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `http://localhost:9000/mcp` | MCP server (streamable-http transport) |
| `http://localhost:9000/realms/mcp/...` | Keycloak OAuth endpoints |
| `http://localhost:9000/.well-known/oauth-authorization-server` | OAuth discovery |
| `http://localhost:9090` | Keycloak admin console (direct) |

## MCP Tools

The server exposes two example tools:

- **hello(name: str)**: Returns a greeting
- **add(a: int, b: int)**: Adds two numbers

## Usage

### Option 1: Direct Token Authentication

Use the pre-generated token from `.env`:

```python
from fastmcp import Client
import os
from dotenv import load_dotenv

load_dotenv()

async with Client("http://localhost:9000/mcp", auth=os.getenv("MCP_TOKEN")) as client:
    result = await client.call_tool("hello", {"name": "World"})
```

### Option 2: OAuth Flow

The server supports the full OAuth authorization code flow. Configure your MCP client to use:

- **Authorization endpoint**: `http://localhost:9000/realms/mcp/protocol/openid-connect/auth`
- **Token endpoint**: `http://localhost:9000/realms/mcp/protocol/openid-connect/token`
- **Client ID**: `mcp-server`

### Exposing to the Internet

To use with external clients, expose the server via a tunnel:

```bash
ngrok http 9000
```

Then update the client URL to use the ngrok URL:

```python
async with Client("https://your-ngrok-url.ngrok-free.app/mcp", auth=TOKEN) as client:
    ...
```

## Development

### Local Setup

```bash
# Create virtual environment
uv venv
source .venv/bin/activate

# Install dependencies
uv sync

# Run server locally (requires Keycloak running)
uv run python server.py
```

### Rebuild MCP Server Container

```bash
docker compose build mcp-server
docker compose up -d mcp-server
```

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f mcp-server
```

### Reset Everything

```bash
docker compose down -v
docker compose up -d
```

## Project Structure

```
.
├── docker-compose.yml    # Service orchestration
├── Dockerfile            # MCP server container
├── nginx.conf            # Reverse proxy configuration
├── server.py             # FastMCP server with JWT auth
├── client.py             # Test client
├── setup_keycloak.py     # Keycloak configuration (run by keycloak-init)
├── realm-export.json     # Keycloak realm import config
├── pyproject.toml        # Python dependencies
└── .env                  # Generated credentials (gitignored)
```

## Security Notes

- The default configuration uses development settings (no SSL, simple passwords)
- For production, enable HTTPS via nginx and use strong credentials
- The `.env` file contains secrets and should not be committed to version control
- JWT issuer/audience validation is disabled by default to support proxies; enable in production

## Troubleshooting

**Keycloak not starting**: Wait longer or check logs with `docker compose logs keycloak`

**Token verification fails**: Ensure the JWKS endpoint is accessible from the MCP server container

**"Invalid redirect URI"**: Add your callback URL to the client's redirect URIs in Keycloak admin

**Connection refused**: Make sure all containers are running with `docker compose ps`
