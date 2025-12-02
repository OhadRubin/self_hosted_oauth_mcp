# OAuth setup for fastmcp

**User:** Anonymous (ohadr@mail.tau.ac.il)  
**Created:** 12/2/2025 11:58:48  
**Updated:** 12/2/2025 12:15:30  
**Exported:** 12/2/2025 12:17:06  
**Link:** [https://chatgpt.com/c/692eb857-bc0c-832a-b4ff-b0eba86730ea](https://chatgpt.com/c/692eb857-bc0c-832a-b4ff-b0eba86730ea)  

## Prompt:
give me a minimal OAuth setup for a mcp with streamable-http transports in python using fastmcp.

## Response:
Thought for 1m 4s

here’s a minimal oauth-protected fastmcp server using the streamable http transport, plus a tiny client to hit it.

* * *

### 1\. install

```bash
pip install fastmcp
```

* * *

### 2\. oauth-protected mcp server (streamable http)

this example uses github oauth via the built-in `GitHubProvider`. [fastmcp.mintlify.app+1](https://fastmcp.mintlify.app/servers/auth/authentication)

**env (e.g. `.env`):**

```bash
export FASTMCP_SERVER_AUTH=fastmcp.server.auth.providers.github.GitHubProvider
export FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID="Ov23liAbcDefGhiJkLmN"
export FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET="github_pat_..."
export FASTMCP_SERVER_AUTH_GITHUB_BASE_URL="http://localhost:8000"
```

**server (`server.py`):**

```python
from fastmcp import FastMCP

# auth is configured from environment via FASTMCP_SERVER_AUTH_*
mcp = FastMCP(name="github-secured-server")

@mcp.tool
async def hello(name: str) -> str:
    """echo back a greeting"""
    return f"hello, {name}"

if __name__ == "__main__":
    # streamable-http transport
    mcp.run(transport="http", host="127.0.0.1", port=8000)
    # endpoint: http://localhost:8000/mcp
```

run it:

```bash
fastmcp run server.py --transport http --port 8000
```

either the explicit `mcp.run(transport="http", ...)` or the cli `--transport http` gives you the streamable-http transport at `/mcp`. [fastmcp.mintlify.app](https://fastmcp.mintlify.app/deployment/running-server)

* * *

### 3\. minimal oauth client

fastmcp’s client can drive the oauth flow for http transports via the `auth="oauth"` shortcut or the `OAuth` helper. [fastmcp.mintlify.app](https://fastmcp.mintlify.app/clients/auth/oauth?utm_source=chatgpt.com)

```python
# client.py
import asyncio
from fastmcp import Client

async def main() -> None:
    async with Client("http://localhost:8000/mcp", auth="oauth") as client:
        # on first run this will open a browser for github login
        tools = await client.list_tools()
        print("tools:", [t.name for t in tools])

        result = await client.call_tool("hello", {"name": "ohad"})
        print("hello ->", result.content[0].text)

if __name__ == "__main__":
    asyncio.run(main())
```

run:

```bash
python client.py
```

* * *

### 4\. if you want everything hardcoded in python instead of envs

you can swap the env-based auth for an explicit provider:

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.github import GitHubProvider

auth = GitHubProvider(
    client_id="Ov23liAbcDefGhiJkLmN",
    client_secret="github_pat_...",
    base_url="http://localhost:8000",
)

mcp = FastMCP(name="github-secured-server", auth=auth)
```

rest of the file stays the same; you still run with `transport="http"` as above. [FastMCP](https://gofastmcp.com/integrations/github)

if you tell me which idp you use (github, google, workos, etc.), i can tweak this to the exact provider and scopes.

## Prompt:
ok, but if i need to go to a different website thats not minimal

## Response:
Thought for 1m 20s

yeah, exactly — **real oauth means “go to another website” by design** (the idp login page). if you _never_ want to leave your own process, you don’t really want oauth, you want **“just give me a bearer token and check it”**.

here’s the closest thing to “minimal oauth-ish auth” that:

*   works with streamable-http
*   stays on your machine
*   has zero extra services, just a shared bearer token

* * *

1\. minimal secured fastmcp server (static bearer token, streamable-http)
-------------------------------------------------------------------------

this uses `StaticTokenVerifier`, which is literally “accept this one hard-coded token and give it some scopes”. [fastmcp.mintlify.app](https://fastmcp.mintlify.app/servers/auth/token-verification)

**server.py**

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

# accept exactly this token with a "basic" scope
verifier = StaticTokenVerifier(
    tokens={
        "dev-token-123": {
            "client_id": "local-dev-client",
            "scopes": ["basic"],
        }
    },
    required_scopes=["basic"],
)

mcp = FastMCP(
    name="minimal-secured",
    auth=verifier,  # no oauth server, just token verification
)

@mcp.tool
async def hello(name: str) -> str:
    """say hello"""
    return f"hello, {name}"

if __name__ == "__main__":
    # streamable-http transport
    mcp.run(
        transport="http",  # this is the "streamable-http" transport
        host="127.0.0.1",
        port=8000,
        path="/mcp",
    )
```

run it:

```bash
pip install fastmcp
python server.py
```

* * *

2\. minimal client using that bearer token
------------------------------------------

fastmcp’s client will stick whatever you put in `auth` into an `Authorization: Bearer <token>` header for streamable-http. [fastmcp.mintlify.app](https://fastmcp.mintlify.app/clients/auth/bearer?utm_source=chatgpt.com)

**client.py**

```python
import asyncio
from fastmcp import Client

TOKEN = "dev-token-123"  # must match server's StaticTokenVerifier

async def main() -> None:
    async with Client(
        "http://127.0.0.1:8000/mcp",
        auth=TOKEN,  # becomes Authorization: Bearer dev-token-123
    ) as client:
        tools = await client.list_tools()
        print("tools:", [t.name for t in tools])

        result = await client.call_tool("hello", {"name": "ohad"})
        # mcp result payload -> take first text chunk
        print("hello ->", result.content[0].text)

if __name__ == "__main__":
    asyncio.run(main())
```

run:

```bash
python client.py
```

no redirects, no discovery, no oauth server, no browser. just:

*   server: “i only accept bearer `dev-token-123` with scope `basic`”
*   client: “here’s bearer `dev-token-123` on every request”

* * *

3\. where “real oauth” fits
---------------------------

if you later want _actual_ oauth (auth0, workos, scalekit, etc.), you swap `StaticTokenVerifier` for one of:

*   `JWTVerifier` / `IntrospectionTokenVerifier` (token verification against a real issuer) [fastmcp.mintlify.app](https://fastmcp.mintlify.app/servers/auth/token-verification)
*   or a `RemoteAuthProvider` / oauth proxy (full discovery + login flow) [fastmcp.mintlify.app+1](https://fastmcp.mintlify.app/servers/auth/authentication?utm_source=chatgpt.com)

but **those always imply “some other http endpoint exists that does login + token issuing”**, which is what you’re trying to avoid right now.

* * *

if you want, i can give you the same pattern but with a symmetric hs256 jwt instead of a raw string token, still with no external website.

## Prompt:
does it work with https?

## Response:
yeah.

the `fastmcp` http transport is just an http server (under the hood uvicorn / fastapi style), so https works in the usual two ways:

* * *

### 1\. terminate tls in a reverse proxy (recommended)

run your mcp server exactly as before:

```python
mcp.run(
    transport="http",
    host="127.0.0.1",
    port=8000,
    path="/mcp",
)
```

then put nginx / caddy / traefik in front:

*   proxy `https://your-domain.com/mcp` → `http://127.0.0.1:8000/mcp`
*   the mcp client connects to `https://your-domain.com/mcp`
*   your bearer token still travels in the `authorization: bearer ...` header, unchanged

this is the usual pattern and you do not need to change fastmcp code.

* * *

### 2\. direct https from python (if you really want)

if you skip a reverse proxy, you need to run the underlying asgi server (uvicorn) with `ssl_keyfile` / `ssl_certfile`. fastmcp itself does not block this; you just need to:

*   either use the `fastmcp` cli if it exposes tls options
*   or wrap the fastmcp app in your own uvicorn run call with ssl parameters

conceptually:

```python
# pseudo-code, not exact api
if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8443,
        ssl_certfile="/path/to/cert.pem",
        ssl_keyfile="/path/to/key.pem",
    )
```

then clients would connect to `https://your-host:8443/mcp`.

* * *

### 3\. auth side

the auth layer we set up (`statictokenverifier` + bearer token) is http/https agnostic:

*   it only inspects headers on the asgi request
*   as long as the request reaches the server, it does not care whether the outer transport was http or https

so: yes, your minimal setup works over https; you just need to provide tls either at the proxy or at the python server.

## Prompt:
ok, so anything easier than github to use?

## Response:
Thought for 1m 0s

short answer: for **real oauth**, nothing is meaningfully “easier” than github – google, discord, etc all have the same “create app → copy client id/secret” step. the only genuinely easier options are:

*   **not really oauth**: static bearer tokens / jwt verification
*   **oauth service built for mcp**: workos authkit / descope via `RemoteAuthProvider`

here’s how i’d think about it.

* * *

### 1\. truly minimal: static bearer token (dev only)

this is the lowest-friction option that still plugs into fastmcp’s auth machinery, but it’s not oauth – it just checks for a known token string and associated scopes. [fastmcp.mintlify.app](https://fastmcp.mintlify.app/servers/auth/token-verification?utm_source=chatgpt.com)

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

verifier = StaticTokenVerifier(
    tokens={
        "dev-token-123": {
            "client_id": "local-dev-client",
            "scopes": ["basic"],
        }
    },
    required_scopes=["basic"],
)

mcp = FastMCP(name="dev-server", auth=verifier)

@mcp.tool
def hello(name: str) -> str:
    return f"hello, {name}"

if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
```

client sends:

```http
authorization: bearer dev-token-123
```

and you’re done. again: **not oauth**, but much easier than any idp.

* * *

### 2\. “easier oauth” in the mcp sense: workos authkit (remote oauth)

for **real oauth** that plays nicely with mcp clients, fastmcp’s docs point you at **remote oauth** with an idp that supports **dynamic client registration** (workos authkit, descope, etc). this avoids a lot of the annoying callback/port games that github/google require and is about as simple as “point at an auth domain”. [fastmcp.mintlify.app](https://fastmcp.mintlify.app/servers/auth/authentication)

example with workos authkit:

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.workos import AuthKitProvider

auth = AuthKitProvider(
    authkit_domain="https://your-project.authkit.app",
    base_url="https://your-fastmcp-server.com",
)

mcp = FastMCP(name="enterprise-server", auth=auth)

@mcp.tool
def hello(name: str) -> str:
    return f"hello, {name}"

if __name__ == "__main__":
    mcp.run(transport="http", port=8000)
```

on the workos side you configure the project once; then mcp clients handle registration and token management automatically.

this is still “go to another website to log in”, but _mcp-wise_ it’s simpler than wrestling directly with github/google because the provider is dcr-friendly. [fastmcp.mintlify.app+1](https://fastmcp.mintlify.app/servers/auth/authentication)

* * *

### 3\. github vs google vs others

fastmcp lists github and google side-by-side as **oauth proxy** providers, and they both need:

*   create oauth app in their dev console
*   copy client id / secret into env
*   set `FASTMCP_SERVER_AUTH=...Provider`

snippet from the docs: [fastmcp.mintlify.app](https://fastmcp.mintlify.app/servers/auth/authentication)

```bash
# github
export FASTMCP_SERVER_AUTH=fastmcp.server.auth.providers.github.GitHubProvider
export FASTMCP_SERVER_AUTH_GITHUB_CLIENT_ID="..."
export FASTMCP_SERVER_AUTH_GITHUB_CLIENT_SECRET="..."

# google
export FASTMCP_SERVER_AUTH=fastmcp.server.auth.providers.google.GoogleProvider
export FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID="..."
export FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET="..."
```

so: **no**, there isn’t a mainstream oauth provider that’s dramatically easier than github in terms of setup – they’re all basically the same flow.

* * *

### practical recommendation

*   if this is for **local dev or a personal tool**: use `StaticTokenVerifier` and be happy.
*   if you want **proper oauth** for multiple users and real clients: pick a dcr-friendly service (workos authkit / descope) and use `RemoteAuthProvider`.
*   only use github/google directly if you already have those apps configured or specifically need those identities.

if you tell me “dev only” vs “eventually multi-user / prod”, i can give you a concrete config for one path and ignore the others.

## Prompt:
can i simply host authkit via docker?

## Response:
Thought for 37s

short answer: **no, not in the “docker run authkit && you’re done” sense.**

workos authkit is a **hosted** auth service:

*   you enable authkit in the workos dashboard and get an authkit domain
*   your app (or fastmcp’s `AuthKitProvider`) redirects to that hosted ui
*   your app exchanges the code with workos and gets a user / tokens back [WorkOS+1](https://workos.com/docs/authkit/authkit?utm_source=chatgpt.com)

there’s no public “authkit server” docker image you can run as a drop-in replacement. the auth you get in fastmcp that mentions authkit assumes that domain is **workos-hosted**, not something you self-host.

you _can_:

*   run **your own app** (frontend + backend) in docker that uses the authkit sdks and workos apis
*   or build your own login ui against workos’ user management apis and containerize _that_ [GitHub](https://github.com/workos/authkit?utm_source=chatgpt.com)

…but the underlying auth backend is still workos’ cloud.

* * *

### if you want fully self-hosted oauth in docker

for a setup where everything is under your control, the pattern with fastmcp is:

1.  pick a self-hosted oidc provider that has a docker image, for example:
    *   keycloak
    *   authentik
    *   zitadel
2.  run it via docker / compose

example (keycloak):

```yaml
# docker-compose.yml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:25.0.0
    command: start-dev
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
    ports:
      - "8080:8080"
```

configure in the keycloak ui:

*   create realm `mcp`
*   create client `mcp-server` (confidential)
*   set redirect uri to your mcp server if you need browser login
    3.  point fastmcp at the provider using `JWTVerifier`:

```python
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier

verifier = JWTVerifier(
    jwks_uri="http://localhost:8080/realms/mcp/protocol/openid-connect/certs",
    issuer="http://localhost:8080/realms/mcp",
    audience="mcp-server",
)

mcp = FastMCP(name="mcp-keycloak", auth=verifier)

@mcp.tool
async def hello(name: str) -> str:
    return f"hello, {name}"

if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp")
```

fastmcp then:

*   receives `authorization: bearer <jwt>`
*   passes it to `JWTVerifier`
*   verifier fetches keys from `jwks_uri`, checks `issuer` and `audience` and scopes

that gives you:

*   full control (all docker-hosted)
*   standard oauth / oidc
*   no workos dependency

* * *

so:

*   **authkit specifically**: you cannot “just host it in docker”; it’s a managed service.
*   **self-hosted oauth in docker**: use keycloak / authentik / similar + `JWTVerifier` instead of `AuthKitProvider`.

if you tell me which provider you lean toward (keycloak vs authentik, etc.), i can sketch the exact urls and claims layout for fastmcp.



---
Powered by [ChatGPT Exporter](https://www.chatgptexporter.com)