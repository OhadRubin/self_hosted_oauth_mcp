#!/usr/bin/env python3
"""
OAuth client that performs the full MCP OAuth flow:
1. Discovers OAuth endpoints
2. Registers as a dynamic client
3. Opens browser for user login
4. Receives callback with auth code
5. Exchanges code for FastMCP-issued JWT
6. Tests MCP endpoint with the token
"""

import asyncio
import hashlib
import secrets
import webbrowser
from urllib.parse import urlencode, parse_qs, urlparse
import httpx
from aiohttp import web


class OAuthClient:
    def __init__(self, mcp_url: str):
        self.mcp_url = mcp_url.rstrip("/")
        self.callback_port = 8765
        self.callback_path = "/callback"
        self.redirect_uri = f"http://localhost:{self.callback_port}{self.callback_path}"

        # OAuth state
        self.client_id = None
        self.client_secret = None
        self.auth_code = None
        self.code_verifier = None
        self.state = None

        # Tokens
        self.access_token = None
        self.refresh_token = None

        # Discovered endpoints
        self.authorization_endpoint = None
        self.token_endpoint = None
        self.registration_endpoint = None

    async def discover(self):
        """Discover OAuth endpoints from the MCP server."""
        print(f"\n[1] Discovering OAuth endpoints from {self.mcp_url}...")

        async with httpx.AsyncClient() as client:
            # Get authorization server metadata
            resp = await client.get(f"{self.mcp_url}/.well-known/oauth-authorization-server")
            resp.raise_for_status()
            metadata = resp.json()

            self.authorization_endpoint = metadata["authorization_endpoint"]
            self.token_endpoint = metadata["token_endpoint"]
            self.registration_endpoint = metadata.get("registration_endpoint")

            print(f"    Authorization: {self.authorization_endpoint}")
            print(f"    Token: {self.token_endpoint}")
            print(f"    Registration: {self.registration_endpoint}")

    async def register_client(self):
        """Register as a dynamic OAuth client."""
        print(f"\n[2] Registering as dynamic client...")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.registration_endpoint,
                json={
                    "client_name": "oauth_client.py",
                    "redirect_uris": [self.redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_post",
                }
            )
            resp.raise_for_status()
            data = resp.json()

            self.client_id = data["client_id"]
            self.client_secret = data.get("client_secret")

            print(f"    Client ID: {self.client_id}")
            print(f"    Client Secret: {self.client_secret[:20]}..." if self.client_secret else "    No secret")

    def generate_pkce(self):
        """Generate PKCE code verifier and challenge."""
        self.code_verifier = secrets.token_urlsafe(32)
        digest = hashlib.sha256(self.code_verifier.encode()).digest()
        self.code_challenge = secrets.token_urlsafe(32)
        # Proper S256 challenge
        import base64
        self.code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    async def start_callback_server(self):
        """Start local server to receive OAuth callback."""
        self.auth_code = None
        received_event = asyncio.Event()

        async def callback_handler(request):
            query = request.query
            if "code" in query:
                self.auth_code = query["code"]
                received_state = query.get("state")
                if received_state != self.state:
                    return web.Response(text="State mismatch!", status=400)
                received_event.set()
                return web.Response(
                    text="<html><body><h1>Success!</h1><p>You can close this window.</p></body></html>",
                    content_type="text/html"
                )
            elif "error" in query:
                error = query.get("error")
                desc = query.get("error_description", "")
                received_event.set()
                return web.Response(text=f"Error: {error} - {desc}", status=400)
            return web.Response(text="Missing code", status=400)

        app = web.Application()
        app.router.add_get(self.callback_path, callback_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self.callback_port)
        await site.start()

        return runner, received_event

    async def authorize(self):
        """Open browser for user authorization."""
        print(f"\n[3] Starting authorization flow...")

        self.generate_pkce()
        self.state = secrets.token_urlsafe(16)

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
        }

        auth_url = f"{self.authorization_endpoint}?{urlencode(params)}"
        print(f"    Opening browser for login...")
        print(f"    URL: {auth_url[:80]}...")

        # Start callback server
        runner, received_event = await self.start_callback_server()

        try:
            # Open browser
            webbrowser.open(auth_url)

            print(f"\n    Waiting for callback on http://localhost:{self.callback_port}{self.callback_path}")
            print(f"    (Login with user/password in the browser)")

            # Wait for callback (timeout 120s)
            await asyncio.wait_for(received_event.wait(), timeout=120)

            if self.auth_code:
                print(f"    Received auth code: {self.auth_code[:20]}...")
            else:
                raise Exception("No auth code received")
        finally:
            await runner.cleanup()

    async def exchange_code(self):
        """Exchange authorization code for tokens."""
        print(f"\n[4] Exchanging code for tokens...")

        async with httpx.AsyncClient() as client:
            data = {
                "grant_type": "authorization_code",
                "code": self.auth_code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "code_verifier": self.code_verifier,
            }
            if self.client_secret:
                data["client_secret"] = self.client_secret

            resp = await client.post(self.token_endpoint, data=data)

            if resp.status_code != 200:
                print(f"    Error: {resp.status_code} - {resp.text}")
                raise Exception(f"Token exchange failed: {resp.text}")

            tokens = resp.json()
            self.access_token = tokens["access_token"]
            self.refresh_token = tokens.get("refresh_token")

            print(f"    Access token: {self.access_token[:50]}...")
            if self.refresh_token:
                print(f"    Refresh token: {self.refresh_token[:30]}...")

    async def test_mcp(self):
        """Test the MCP endpoint with the access token using FastMCP Client."""
        print(f"\n[5] Testing MCP endpoint with FastMCP Client...")

        from fastmcp import Client

        async with Client(
            f"{self.mcp_url}/mcp",
            auth=self.access_token,
        ) as client:
            # List available tools
            tools = await client.list_tools()
            print(f"    Available tools: {[t.name for t in tools]}")

            # Call hello tool
            result = await client.call_tool("hello", {"name": "OAuth User"})
            print(f"    hello -> {result.content[0].text}")

            # Call add tool
            result = await client.call_tool("add", {"a": 5, "b": 3})
            print(f"    add(5, 3) -> {result.content[0].text}")

            print("\n    ✓ MCP authentication and tool calls successful!")

    async def run(self):
        """Run the full OAuth flow."""
        print("=" * 60)
        print("MCP OAuth Client")
        print("=" * 60)

        await self.discover()
        await self.register_client()
        await self.authorize()
        await self.exchange_code()
        await self.test_mcp()

        print("\n" + "=" * 60)
        print("Tokens saved. You can use this access token:")
        print("=" * 60)
        print(f"\nAccess Token:\n{self.access_token}\n")


async def main():
    import sys

    # Default to ngrok URL or localhost
    if len(sys.argv) > 1:
        mcp_url = sys.argv[1]
    else:
        mcp_url = "http://localhost:9000"

    client = OAuthClient(mcp_url)
    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
