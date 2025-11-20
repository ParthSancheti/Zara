# 1. Use a lightweight Python setup
FROM python:3.10-slim

# 2. Set the working directory
WORKDIR /app
COPY . /app

# 3. Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 4. Start the bot
CMD ["python", "main.py"]
