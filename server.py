import os
import time
import secrets
import hashlib
from base64 import urlsafe_b64encode
from typing import Any
from fastmcp import FastMCP
from fastmcp.server.auth.oidc_proxy import OIDCProxy
from fastmcp.server.auth.oauth_proxy import create_error_html, DEFAULT_AUTH_CODE_EXPIRY_SECONDS
from authlib.integrations.httpx_client import AsyncOAuth2Client
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.middleware import Middleware
from urllib.parse import urlencode


# Keycloak configuration from environment
# KEYCLOAK_URL is used for internal JWKS fetching (token validation)
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://localhost:9090")
# KEYCLOAK_PUBLIC_URL is used for OIDC config (authorization endpoints visible to browser)
# Should go through nginx so X-Forwarded headers work
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", "http://nginx:80")
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

    # Internal URLs that need rewriting to public URLs
    INTERNAL_URLS = [
        "http://nginx:80",
        "http://nginx",
        "http://keycloak:9090",
        "http://localhost:9000",
        PLACEHOLDER_BASE_URL,
    ]

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        base_url = get_base_url_from_request(request)

        # URL-encoded versions for rewriting redirect_uri parameters
        from urllib.parse import quote
        placeholder_encoded = quote(PLACEHOLDER_BASE_URL, safe='')
        base_url_encoded = quote(base_url, safe='')

        content_type = ""

        def rewrite_urls(text: str) -> str:
            """Rewrite all internal URLs to use the public base URL."""
            for internal_url in self.INTERNAL_URLS:
                text = text.replace(internal_url, base_url)
                # Also handle URL-encoded versions
                text = text.replace(quote(internal_url, safe=''), base_url_encoded)
            return text

        async def send_wrapper(message):
            nonlocal content_type

            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                new_headers = []

                for name, value in headers:
                    name_lower = name.lower()
                    if name_lower == b"www-authenticate":
                        # Rewrite resource_metadata URL
                        value_str = rewrite_urls(value.decode())
                        value = value_str.encode()
                    elif name_lower == b"location":
                        # Rewrite redirect URLs
                        value_str = rewrite_urls(value.decode())
                        value = value_str.encode()
                    elif name_lower == b"content-type":
                        content_type = value.decode()
                    new_headers.append((name, value))

                message = {**message, "headers": new_headers}
                await send(message)

            elif message["type"] == "http.response.body":
                body = message.get("body", b"")

                # Rewrite JSON responses containing internal URLs
                if "application/json" in content_type:
                    body_str = body.decode()
                    for internal_url in self.INTERNAL_URLS:
                        if internal_url in body_str:
                            body_str = rewrite_urls(body_str)
                            body = body_str.encode()
                            break
                    message = {**message, "body": body}

                await send(message)
            else:
                await send(message)

        await self.app(scope, receive, send_wrapper)


class DynamicOIDCProxy(OIDCProxy):
    """OIDCProxy with dynamic URL support for ngrok/proxy environments."""

    def get_token_verifier(
        self,
        *,
        algorithm: str | None = None,
        audience: str | None = None,
        required_scopes: list[str] | None = None,
        timeout_seconds: int | None = None,
    ):
        """Override to skip issuer validation for dynamic URL environments.

        When using proxies like ngrok, the issuer in the token will be the public
        URL (e.g., https://xxx.ngrok-free.app/realms/mcp), but the OIDC config
        was fetched from an internal URL with a different issuer. This mismatch
        causes token validation to fail.

        By setting issuer=None, we skip issuer validation but still validate
        the token signature against the JWKS.
        """
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        print(f"[DEBUG] Creating JWTVerifier with jwks_uri={self.oidc_config.jwks_uri}, issuer=None (skipped for dynamic URLs)")

        return JWTVerifier(
            jwks_uri=str(self.oidc_config.jwks_uri),
            issuer=None,  # Skip issuer validation for dynamic URL support
            algorithm=algorithm,
            audience=audience,
            required_scopes=required_scopes,
        )

    async def _handle_idp_callback(
        self, request: Request
    ) -> HTMLResponse | RedirectResponse:
        """Handle callback from upstream IdP with dynamic URL support.

        Override to use dynamic base URL from request headers for token exchange,
        instead of the static self.base_url. This is needed for ngrok/proxy setups
        where the redirect_uri in the authorization request gets rewritten.
        """
        from fastmcp.server.auth.oauth_proxy import ClientCode

        try:
            idp_code = request.query_params.get("code")
            txn_id = request.query_params.get("state")
            error = request.query_params.get("error")

            if error:
                error_description = request.query_params.get("error_description")
                print(f"[ERROR] IdP callback error: {error} - {error_description}")
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message=f"Authentication failed: {error_description or 'Unknown error'}",
                    error_details={"Error Code": error} if error else None,
                )
                return HTMLResponse(content=html_content, status_code=400)

            if not idp_code or not txn_id:
                print("[ERROR] IdP callback missing code or transaction ID")
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message="Missing authorization code or transaction ID from the identity provider.",
                )
                return HTMLResponse(content=html_content, status_code=400)

            # Look up transaction data
            transaction_model = await self._transaction_store.get(key=txn_id)
            if not transaction_model:
                print(f"[ERROR] IdP callback with invalid transaction ID: {txn_id}")
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message="Invalid or expired authorization transaction. Please try authenticating again.",
                )
                return HTMLResponse(content=html_content, status_code=400)
            transaction = transaction_model.model_dump()

            # Exchange IdP code for tokens (server-side)
            oauth_client = AsyncOAuth2Client(
                client_id=self._upstream_client_id,
                client_secret=self._upstream_client_secret.get_secret_value(),
                token_endpoint_auth_method=self._token_endpoint_auth_method,
                timeout=30,
            )

            try:
                # DYNAMIC URL: Use request headers instead of static self.base_url
                dynamic_base_url = get_base_url_from_request(request)
                idp_redirect_uri = f"{dynamic_base_url.rstrip('/')}{self._redirect_path}"

                print(f"[DEBUG] Token exchange redirect_uri: {idp_redirect_uri}")
                print(f"[DEBUG] Request headers - x-forwarded-proto: {request.headers.get('x-forwarded-proto')}, x-forwarded-host: {request.headers.get('x-forwarded-host')}")

                # Build token exchange parameters
                token_params: dict[str, Any] = {
                    "url": self._upstream_token_endpoint,
                    "code": idp_code,
                    "redirect_uri": idp_redirect_uri,
                }

                # Include proxy's code_verifier if we forwarded PKCE
                proxy_code_verifier = transaction.get("proxy_code_verifier")
                if proxy_code_verifier:
                    token_params["code_verifier"] = proxy_code_verifier
                    print(f"[DEBUG] Including proxy code_verifier in token exchange for transaction {txn_id}")

                # Add any extra token parameters configured for this proxy
                if self._extra_token_params:
                    token_params.update(self._extra_token_params)

                idp_tokens: dict[str, Any] = await oauth_client.fetch_token(**token_params)
                print(f"[DEBUG] Successfully exchanged IdP code for tokens (transaction: {txn_id})")

            except Exception as e:
                print(f"[ERROR] IdP token exchange failed: {e}")
                html_content = create_error_html(
                    error_title="OAuth Error",
                    error_message=f"Token exchange with identity provider failed: {e}",
                )
                return HTMLResponse(content=html_content, status_code=500)

            # Generate our own authorization code for the client
            client_code = secrets.token_urlsafe(32)
            code_expires_at = int(time.time() + DEFAULT_AUTH_CODE_EXPIRY_SECONDS)

            # Store client code with PKCE challenge and IdP tokens
            await self._code_store.put(
                key=client_code,
                value=ClientCode(
                    code=client_code,
                    client_id=transaction["client_id"],
                    redirect_uri=transaction["client_redirect_uri"],
                    code_challenge=transaction["code_challenge"],
                    code_challenge_method=transaction["code_challenge_method"],
                    scopes=transaction["scopes"],
                    idp_tokens=idp_tokens,
                    expires_at=code_expires_at,
                    created_at=time.time(),
                ),
                ttl=DEFAULT_AUTH_CODE_EXPIRY_SECONDS,
            )

            # Clean up transaction
            await self._transaction_store.delete(key=txn_id)

            # Build client callback URL with our code and original state
            client_redirect_uri = transaction["client_redirect_uri"]
            client_state = transaction["client_state"]

            callback_params = {
                "code": client_code,
                "state": client_state,
            }

            # Add query parameters to client redirect URI
            separator = "&" if "?" in client_redirect_uri else "?"
            client_callback_url = (
                f"{client_redirect_uri}{separator}{urlencode(callback_params)}"
            )

            print(f"[DEBUG] Forwarding to client callback for transaction {txn_id}")

            return RedirectResponse(url=client_callback_url, status_code=302)

        except Exception as e:
            print(f"[ERROR] Error in IdP callback handler: {e}")
            import traceback
            traceback.print_exc()
            html_content = create_error_html(
                error_title="OAuth Error",
                error_message="Internal server error during OAuth callback processing. Please try again.",
            )
            return HTMLResponse(content=html_content, status_code=500)

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """Override routes to support dynamic base URL from request headers."""
        # Get the standard OIDCProxy routes
        routes = super().get_routes(mcp_path)

        # Debug endpoint to check headers
        async def debug_headers(request: Request):
            return JSONResponse({
                "headers": dict(request.headers),
                "url": str(request.url),
                "base_url": get_base_url_from_request(request),
            })

        # Override the protected resource metadata endpoint to be dynamic
        async def dynamic_protected_resource_metadata(request: Request):
            # Debug: log incoming headers
            print(f"[DEBUG] Protected resource metadata request headers: {dict(request.headers)}")
            print(f"[DEBUG] Computed base_url: {get_base_url_from_request(request)}")
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
        # Debug endpoint
        new_routes.append(Route(
            "/debug",
            endpoint=debug_headers,
            methods=["GET"],
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
# Use KEYCLOAK_PUBLIC_URL for OIDC config so authorization URLs go through nginx
auth_provider = DynamicOIDCProxy(
    config_url=f"{KEYCLOAK_PUBLIC_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration",
    client_id=KEYCLOAK_CLIENT_ID,
    client_secret=KEYCLOAK_CLIENT_SECRET,
    base_url=PLACEHOLDER_BASE_URL,  # Will be rewritten by middleware
    require_authorization_consent=False,  # Keycloak handles consent
)

mcp = FastMCP(name="mcp-keycloak", auth=auth_provider)


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
