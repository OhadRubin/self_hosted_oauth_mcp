FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project files
COPY pyproject.toml uv.lock ./
COPY server.py ./

# Install dependencies
RUN uv sync --no-dev

# Expose the MCP server port
EXPOSE 9000

# Run the server
CMD ["uv", "run", "python", "server.py"]
