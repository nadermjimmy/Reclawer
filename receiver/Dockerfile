FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# "::" binds IPv6 + IPv4 - required for Railway private networking
CMD ["uvicorn", "app:app", "--host", "::", "--port", "8000"]
