FROM python:3.12-slim

# git is required: the agent clones repositories and pushes fix branches.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py a2a_server.py ./

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD exec uvicorn a2a_server:app --host 0.0.0.0 --port ${PORT}
