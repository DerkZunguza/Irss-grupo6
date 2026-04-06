# Dockerfile — Backend Flask para monitoramento de hardware ESP32-S3
FROM python:3.11-slim

# Diretório de trabalho dentro do container
WORKDIR /app

# Copiar e instalar dependências primeiro (aproveita cache do Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fonte
COPY . .

# Expor porta do Flask
EXPOSE 5000

# Iniciar com Gunicorn em produção (4 workers)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "60", "app:app"]
