import os
from fastmcp import FastMCP
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.middleware import Middleware


# Keycloak configuration from environment
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:9090")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "mcp")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "mcp-server")
KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "")
MCP_PATH = "/mcp"

# Placeholder that will be rewritten by middleware
PLACEHOLDER_BASE_URL = "http://localhost:9000"


def get_base_url_from_request(request: Request) -> str:
    """Extract base URL from request headers (supports proxies like ngrok)."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "localhost:9000")
    return f"{proto}://{host}"


class DynamicUrlMiddleware:
    """Middleware that rewrites placeholder URLs to use the actual request origin."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        base_url = get_base_url_from_request(request)

        # URL-encoded version for rewriting redirect_uri parameters
        from urllib.parse import quote
        placeholder_encoded = quote(PLACEHOLDER_BASE_URL, safe='')
        base_url_encoded = quote(base_url, safe='')

        content_type = ""

        async def send_wrapper(message):
            nonlocal content_type

            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                new_headers = []

                for name, value in headers:
                    name_lower = name.lower()
                    if name_lower == b"www-authenticate":
                        # Rewrite resource_metadata URL
                        value_str = value.decode()
                        value_str = value_str.replace(PLACEHOLDER_BASE_URL, base_url)
                        value = value_str.encode()
                    elif name_lower == b"location":
                        # Rewrite redirect URLs (both plain and URL-encoded)
                        value_str = value.decode()
                        value_str = value_str.replace(placeholder_encoded, base_url_encoded)
                        value_str = value_str.replace(PLACEHOLDER_BASE_URL, base_url)
                        value = value_str.encode()
                    elif name_lower == b"content-type":
                        content_type = value.decode()
                    new_headers.append((name, value))

                message = {**message, "headers": new_headers}
                await send(message)

            elif message["type"] == "http.response.body":
                body = message.get("body", b"")

                # Rewrite JSON responses containing placeholder URLs
                if "application/json" in content_type and PLACEHOLDER_BASE_URL.encode() in body:
                    body = body.replace(PLACEHOLDER_BASE_URL.encode(), base_url.encode())
                    message = {**message, "body": body}

                await send(message)
            else:
                await send(message)

        await self.app(scope, receive, send_wrapper)


class DynamicOIDCProxy(OIDCProxy):
    """OIDCProxy with dynamic URL support for ngrok/proxy environments."""

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Override routes to support dynamic base URL from request headers."""
        # Get the standard OIDCProxy routes
        routes = super().get_routes(mcp_path)

        # Override the protected resource metadata endpoint to be dynamic
        async def dynamic_protected_resource_metadata(request: Request):
            base_url = get_base_url_from_request(request)
            token_verifier = self.get_token_verifier()
            scopes = []
            if token_verifier and hasattr(token_verifier, 'required_scopes') and token_verifier.required_scopes:
                scopes = token_verifier.required_scopes
            return JSONResponse({
                "resource": f"{base_url}{mcp_path or MCP_PATH}",
                "authorization_servers": [base_url],
                "scopes_supported": scopes,
                "bearer_methods_supported": ["header"],
            })

        # Override the OAuth authorization server metadata endpoint
        async def dynamic_authorization_server_metadata(request: Request):
            base_url = get_base_url_from_request(request)
            return JSONResponse({
                "issuer": base_url,
                "authorization_endpoint": f"{base_url}/authorize",
                "token_endpoint": f"{base_url}/token",
                "registration_endpoint": f"{base_url}/register",
                "jwks_uri": f"{base_url}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/certs",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
                "code_challenge_methods_supported": ["S256"],
            })

        # Replace or add our dynamic routes
        new_routes = []
        for route in routes:
            # Skip routes we're replacing with dynamic versions
            if hasattr(route, 'path'):
                if '/.well-known/oauth-protected-resource' in route.path:
                    continue
                if '/.well-known/oauth-authorization-server' in route.path:
                    continue
            new_routes.append(route)

        # Add our dynamic endpoints
        new_routes.append(Route(
            f"/.well-known/oauth-protected-resource{mcp_path or MCP_PATH}",
            endpoint=dynamic_protected_resource_metadata,
            methods=["GET", "OPTIONS"],
        ))
        new_routes.append(Route(
            "/.well-known/oauth-authorization-server",
            endpoint=dynamic_authorization_server_metadata,
            methods=["GET", "OPTIONS"],
        ))

        return new_routes


# Load client secret from environment or .env file
if not KEYCLOAK_CLIENT_SECRET:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "")
    except ImportError:
        pass

if not KEYCLOAK_CLIENT_SECRET:
    print("WARNING: KEYCLOAK_CLIENT_SECRET not set. OAuth will not work properly.")

# Create the OIDC Proxy auth provider
auth_provider = DynamicOIDCProxy(
    config_url=f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration",
    client_id=KEYCLOAK_CLIENT_ID,
    client_secret=KEYCLOAK_CLIENT_SECRET,
    base_url=PLACEHOLDER_BASE_URL,  # Will be rewritten by middleware
    require_authorization_consent=False,  # Keycloak handles consent
)

mcp = FastMCP(name="mcp-keycloak", auth=auth_provider)


# Debug endpoint to check headers
async def debug_headers(request: Request):
    return JSONResponse({
        "headers": dict(request.headers),
        "url": str(request.url),
        "base_url": get_base_url_from_request(request),
    })


@mcp.tool
async def hello(name: str) -> str:
    """Echo back a greeting"""
    return f"hello, {name}"


@mcp.tool
async def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8007,
        path="/mcp",
        middleware=[Middleware(DynamicUrlMiddleware)],
    )
