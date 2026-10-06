FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# "" binds all addresses (IPv4 + IPv6) - "::" alone is IPv6-only under uvicorn
CMD ["uvicorn", "app:app", "--host", "", "--port", "8000"]
