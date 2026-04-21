FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY dbt/  ./dbt/

# Fix: thêm src/ vào PYTHONPATH
ENV PYTHONPATH=/app/src:/app/src/tools

RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["python", "src/server.py"]