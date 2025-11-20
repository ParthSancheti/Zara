# Use an official Python runtime
FROM python:3.10-slim

# 1. Install Chrome and dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 2. Set up the app
WORKDIR /app
COPY . /app

# 3. Install Python libraries
RUN pip install --no-cache-dir -r requirements.txt

# 4. Run the bot
CMD ["python", "main.py"]