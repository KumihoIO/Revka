FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py a2a_server.py ./
COPY grounding/ ./grounding/

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD exec uvicorn a2a_server:app --host 0.0.0.0 --port ${PORT}
