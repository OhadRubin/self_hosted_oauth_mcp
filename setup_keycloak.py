#!/usr/bin/env python3
"""
Setup script to configure Keycloak for the MCP server.

Run this after starting the containers:
    docker compose up -d
    uv run python setup_keycloak.py

This script will:
1. Create a realm called 'mcp'
2. Create a client called 'mcp-server' with client credentials grant
3. Print the client secret for use with the MCP client
"""

import httpx
import os
import sys
from dotenv import set_key

KEYCLOAK_ADMIN_URL = os.getenv("KEYCLOAK_ADMIN_URL", "http://localhost:9090")
KEYCLOAK_PUBLIC_URL = os.getenv("KEYCLOAK_PUBLIC_URL", "http://localhost:9000")
ADMIN_USER = os.getenv("KEYCLOAK_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "admin")
REALM_NAME = os.getenv("KEYCLOAK_REALM", "mcp")
CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "mcp-server")


def get_admin_token(client: httpx.Client) -> str:
    """Get admin access token."""
    resp = client.post(
        f"{KEYCLOAK_ADMIN_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def create_realm(client: httpx.Client, token: str) -> None:
    """Create the MCP realm."""
    headers = {"Authorization": f"Bearer {token}"}

    # Check if realm exists
    resp = client.get(f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}", headers=headers)
    if resp.status_code == 200:
        print(f"Realm '{REALM_NAME}' already exists")
        return

    # Create realm
    resp = client.post(
        f"{KEYCLOAK_ADMIN_URL}/admin/realms",
        headers=headers,
        json={
            "realm": REALM_NAME,
            "enabled": True,
            "displayName": "MCP Realm",
        },
    )
    resp.raise_for_status()
    print(f"Created realm '{REALM_NAME}'")


def configure_realm(client: httpx.Client, token: str) -> None:
    """Disable SSL requirement and VERIFY_PROFILE for the realm."""
    headers = {"Authorization": f"Bearer {token}"}

    # Disable SSL requirement
    resp = client.put(
        f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}",
        headers=headers,
        json={"sslRequired": "none"},
    )
    resp.raise_for_status()
    print("Disabled SSL requirement")

    # Disable VERIFY_PROFILE required action
    resp = client.put(
        f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}/authentication/required-actions/VERIFY_PROFILE",
        headers=headers,
        json={
            "alias": "VERIFY_PROFILE",
            "name": "Verify Profile",
            "providerId": "VERIFY_PROFILE",
            "enabled": False,
            "defaultAction": False,
            "priority": 90,
        },
    )
    resp.raise_for_status()
    print("Disabled VERIFY_PROFILE")


def create_user(client: httpx.Client, token: str) -> None:
    """Create test user."""
    headers = {"Authorization": f"Bearer {token}"}
    username = "user"
    password = "password"

    # Check if user exists
    resp = client.get(
        f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}/users",
        headers=headers,
        params={"username": username},
    )
    resp.raise_for_status()
    existing = resp.json()

    if existing:
        print(f"User '{username}' already exists")
        return

    # Create user
    resp = client.post(
        f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}/users",
        headers=headers,
        json={
            "username": username,
            "enabled": True,
            "email": "user@example.com",
            "firstName": "MCP",
            "lastName": "User",
            "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
            "requiredActions": [],
        },
    )
    resp.raise_for_status()
    print(f"Created user '{username}'")


def create_client(client: httpx.Client, token: str) -> tuple[str, str]:
    """Create the MCP client and return (client_secret, client_uuid)."""
    headers = {"Authorization": f"Bearer {token}"}

    # Check if client exists
    resp = client.get(
        f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}/clients",
        headers=headers,
        params={"clientId": CLIENT_ID},
    )
    resp.raise_for_status()
    existing = resp.json()

    if existing:
        print(f"Client '{CLIENT_ID}' already exists")
        client_uuid = existing[0]["id"]
    else:
        # Create client
        resp = client.post(
            f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}/clients",
            headers=headers,
            json={
                "clientId": CLIENT_ID,
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "serviceAccountsEnabled": True,  # Enable client credentials grant
                "standardFlowEnabled": True,  # Enable authorization code flow
                "directAccessGrantsEnabled": False,
                "redirectUris": ["http://localhost:*/*"],
                "webOrigins": ["http://localhost"],
                "attributes": {
                    "access.token.lifespan": "3600",
                },
            },
        )
        resp.raise_for_status()
        print(f"Created client '{CLIENT_ID}'")

        # Get the client UUID
        resp = client.get(
            f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}/clients",
            headers=headers,
            params={"clientId": CLIENT_ID},
        )
        resp.raise_for_status()
        client_uuid = resp.json()[0]["id"]

    # Get client secret
    resp = client.get(
        f"{KEYCLOAK_ADMIN_URL}/admin/realms/{REALM_NAME}/clients/{client_uuid}/client-secret",
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()["value"], client_uuid


def get_token_for_client(client: httpx.Client, client_secret: str) -> str:
    """Get an access token using client credentials via nginx."""
    resp = client.post(
        f"{KEYCLOAK_PUBLIC_URL}/realms/{REALM_NAME}/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": client_secret,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


ENV_FILE = os.getenv("ENV_FILE", ".env")


def save_to_env(client_secret: str, access_token: str) -> None:
    """Save configuration to .env file."""
    set_key(ENV_FILE, "KEYCLOAK_CLIENT_ID", CLIENT_ID, quote_mode="never")
    set_key(ENV_FILE, "KEYCLOAK_CLIENT_SECRET", client_secret, quote_mode="never")
    set_key(ENV_FILE, "MCP_TOKEN", access_token, quote_mode="never")
    print(f"Wrote configuration to {ENV_FILE}")


def main() -> None:
    print(f"Connecting to Keycloak at {KEYCLOAK_ADMIN_URL}...")

    with httpx.Client(timeout=30.0) as client:
        try:
            token = get_admin_token(client)
            print("Got admin token")
        except httpx.HTTPError as e:
            print(f"Failed to get admin token: {e}")
            print("Make sure Keycloak is running: docker compose up -d")
            sys.exit(1)

        create_realm(client, token)
        configure_realm(client, token)
        create_user(client, token)
        client_secret, client_uuid = create_client(client, token)

        print(f"\nClient ID: {CLIENT_ID}")
        print(f"Client Secret: {client_secret}")

        # Get a sample token
        print("Getting access token...")
        access_token = get_token_for_client(client, client_secret)

        # Write to .env file
        save_to_env(client_secret, access_token)

        print("\n" + "=" * 60)
        print("Keycloak setup complete!")
        print("=" * 60)
        print("\nTo test the MCP client, run:")
        print("  uv run python client.py")


if __name__ == "__main__":
    main()
