# 1. Use Python
FROM python:3.10-slim

# 2. Set up the app folder
WORKDIR /app
COPY . /app

# 3. Install Python libraries
RUN pip install --no-cache-dir -r requirements.txt

# 4. Run the bot
CMD ["python", "main.py"]
