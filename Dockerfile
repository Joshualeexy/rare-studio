# ==============================================================================
# MoneyPrinter Studio v1.0.0 Docker Container
# ==============================================================================

FROM python:3.11-slim

# Install system dependencies (ffmpeg, fonts, build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-montserrat \
    fonts-liberation \
    fontconfig \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency files
COPY pyproject.toml requirements.txt ./

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Expose Web Studio Gallery port
EXPOSE 5050

# Default entrypoint
ENTRYPOINT ["python", "main.py"]
CMD ["infinite"]
