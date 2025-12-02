# Self-Hosted OAuth MCP Server

A self-hosted MCP (Model Context Protocol) server with OAuth 2.0 authentication using Keycloak as the identity provider. This setup allows you to run an OAuth-protected MCP server entirely on your own infrastructure, with support for dynamic URLs (ngrok, reverse proxies, etc.).

## Features

- Full OAuth 2.0 authorization code flow with PKCE
- Dynamic Client Registration (DCR) support
- Works with ngrok and other reverse proxies
- Keycloak as the identity provider
- FastMCP-issued JWT tokens
- Pre-configured test user and client

## Architecture

```
┌─────────────┐      ┌─────────────────┐      ┌──────────────────┐
│   Client    │─────▶│  nginx (:9000)  │─────▶│  mcp-server      │
│             │      │                 │      │  (:8007)         │
└─────────────┘      │  /mcp ──────────┼─────▶│  FastMCP + OAuth │
                     │                 │      └──────────────────┘
                     │  /realms/* ─────┼─────▶┌──────────────────┐
                     │  /authorize     │      │  Keycloak        │
                     │  /token         │      │  (:9090)         │
                     └─────────────────┘      └──────────────────┘
```

- **nginx**: Reverse proxy on port 9000, routes MCP traffic and OAuth endpoints
- **Keycloak**: OAuth 2.0 / OpenID Connect identity provider
- **mcp-server**: FastMCP server with DynamicOIDCProxy for URL-aware authentication

## Prerequisites

- Docker and Docker Compose
- Python 3.11+ (for local development/testing)
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

3. **Test with pre-generated token**:
   ```bash
   uv run python client.py
   ```

4. **Test with full OAuth flow**:
   ```bash
   uv run python oauth_client.py http://localhost:9000
   ```

## Configuration

### Default Credentials

| Setting | Value |
|---------|-------|
| Realm | `mcp` |
| Client ID | `mcp-server` |
| Test User | `user` / `password` |
| Admin Console | http://localhost:9090 (`admin` / `admin`) |

### Environment Variables

The `keycloak-init` service creates a `.env` file with:

| Variable | Description |
|----------|-------------|
| `KEYCLOAK_CLIENT_ID` | OAuth client ID |
| `KEYCLOAK_CLIENT_SECRET` | OAuth client secret (auto-generated) |
| `MCP_TOKEN` | Pre-generated JWT access token for testing |

### Server Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KEYCLOAK_URL` | `http://localhost:9090` | Internal Keycloak URL for JWKS |
| `KEYCLOAK_PUBLIC_URL` | `http://nginx:80` | Keycloak URL through nginx |
| `KEYCLOAK_REALM` | `mcp` | Keycloak realm name |
| `KEYCLOAK_CLIENT_ID` | `mcp-server` | OAuth client ID |

## Usage

### Option 1: Direct Token Authentication

Use the pre-generated token from `.env`:

```python
import asyncio
from fastmcp import Client
from dotenv import load_dotenv
import os

load_dotenv()

async def main():
    async with Client(
        "http://localhost:9000/mcp",
        auth=os.getenv("MCP_TOKEN"),
    ) as client:
        tools = await client.list_tools()
        print("Tools:", [t.name for t in tools])

        result = await client.call_tool("hello", {"name": "World"})
        print(result.content[0].text)

asyncio.run(main())
```

### Option 2: Full OAuth Flow

Run the OAuth client that performs the complete authorization code flow:

```bash
uv run python oauth_client.py http://localhost:9000
```

This will:
1. Discover OAuth endpoints
2. Register as a dynamic client
3. Open browser for user login (use `user` / `password`)
4. Exchange authorization code for tokens
5. Call MCP tools with the access token

## Exposing to the Internet (ngrok)

To use with external clients or test OAuth flows from different origins:

1. **Start ngrok**:
   ```bash
   ngrok http 9000
   ```

2. **Test with ngrok URL**:
   ```bash
   uv run python oauth_client.py https://your-subdomain.ngrok-free.app
   ```

The server automatically detects the public URL from `X-Forwarded-*` headers and adjusts all OAuth URLs accordingly.

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `/mcp` | MCP server (streamable-http transport) |
| `/.well-known/oauth-authorization-server` | OAuth server metadata |
| `/.well-known/oauth-protected-resource/mcp` | Protected resource metadata |
| `/authorize` | OAuth authorization endpoint |
| `/token` | OAuth token endpoint |
| `/register` | Dynamic client registration |
| `/realms/mcp/...` | Keycloak OIDC endpoints (proxied) |
| `/debug` | Debug endpoint showing request headers |

## MCP Tools

The server exposes two example tools:

- **hello(name: str)**: Returns a greeting (`hello, {name}`)
- **add(a: int, b: int)**: Adds two numbers

## Project Structure

```
.
├── docker-compose.yml    # Service orchestration
├── Dockerfile            # MCP server container
├── nginx.conf            # Reverse proxy configuration
├── server.py             # FastMCP server with DynamicOIDCProxy
├── client.py             # Simple test client (uses pre-generated token)
├── oauth_client.py       # Full OAuth flow test client
├── setup_keycloak.py     # Keycloak configuration script
├── realm-export.json     # Keycloak realm import config
├── pyproject.toml        # Python dependencies
└── .env                  # Generated credentials (gitignored)
```

## Development

### Local Setup

```bash
# Install dependencies
uv sync

# Run server locally (requires Keycloak running in Docker)
uv run python server.py
```

### Rebuild MCP Server

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
docker compose logs -f keycloak
```

### Reset Everything

```bash
docker compose down -v
rm -f .env
docker compose up -d
```

## How It Works

### DynamicOIDCProxy

The server uses a custom `DynamicOIDCProxy` class that extends FastMCP's `OIDCProxy` to support dynamic URLs:

1. **Dynamic URL Detection**: Extracts the public URL from `X-Forwarded-Proto` and `X-Forwarded-Host` headers
2. **URL Rewriting Middleware**: Rewrites internal URLs (nginx, keycloak, localhost) to the public URL in responses
3. **Dynamic Token Exchange**: Uses the detected public URL for OAuth token exchange redirect_uri
4. **Flexible Token Validation**: Skips issuer validation to support tokens issued with different public URLs

### OAuth Flow

1. Client discovers OAuth endpoints from `/.well-known/oauth-authorization-server`
2. Client registers dynamically via `/register`
3. Client redirects user to `/authorize` with PKCE challenge
4. User authenticates with Keycloak
5. Keycloak redirects back to MCP server's `/auth/callback`
6. MCP server exchanges code with Keycloak and issues FastMCP JWT
7. Client exchanges authorization code for FastMCP tokens via `/token`
8. Client calls MCP tools with the access token

## Troubleshooting

### "Bearer token rejected"
- Check that Keycloak is healthy: `docker compose ps`
- View token validation logs: `docker compose logs mcp-server`
- The server skips issuer validation for dynamic URL support

### "Invalid redirect_uri"
- Ensure the realm-export.json has `"redirectUris": ["*"]`
- Rebuild after changes: `docker compose down -v && docker compose up -d`

### "Connection refused"
- Wait for all services to start: `docker compose ps`
- Check Keycloak health: `curl http://localhost:9090/health`

### Token exchange fails with ngrok
- Ensure nginx is forwarding X-Forwarded headers
- Check the debug endpoint: `curl https://your-ngrok-url/debug`

## Security Notes

- Default configuration uses development settings (no SSL, simple passwords)
- For production:
  - Enable HTTPS via nginx
  - Use strong credentials
  - Consider enabling issuer validation with a fixed public URL
  - Review Keycloak security settings
- The `.env` file contains secrets and should not be committed

## License

MIT
