FROM python:3.11-slim

WORKDIR /app

# System deps for pip compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && rm -rf /var/lib/apt/lists/*

# Install Python deps (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for runtime data
RUN mkdir -p data logs/agent_logs logs/trade_logs logs/error_logs backtesting/reports

CMD ["python", "main.py"]
