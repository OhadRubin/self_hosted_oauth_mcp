"""
Test client for the OAuth-protected MCP server.

Before running this client, you need to:
1. Start the services: docker compose up -d
2. Run setup: uv run python setup_keycloak.py

The setup script will create a .env file with the token.
"""

import asyncio
import os

from dotenv import load_dotenv
from fastmcp import Client

load_dotenv()

TOKEN = os.getenv("MCP_TOKEN", "")

if not TOKEN:
    print("Error: MCP_TOKEN not found in .env or environment")
    print("Run: uv run python setup_keycloak.py")
    exit(1)


async def main() -> None:
    async with Client(
        # "http://localhost:9000/mcp",
        "https://bc678a635c56.ngrok-free.app/mcp",
        auth=TOKEN,
    ) as client:
        tools = await client.list_tools()
        print("Available tools:", [t.name for t in tools])

        result = await client.call_tool("hello", {"name": "Ohad"})
        print("hello ->", result.content[0].text)

        result = await client.call_tool("add", {"a": 5, "b": 3})
        print("add(5, 3) ->", result.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
