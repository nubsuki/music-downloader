FROM python:3.11-slim

WORKDIR /app

# Copy requirements file and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy ffmpeg binaries from the local directory
COPY ffmpeg/ /app/ffmpeg
RUN chmod +x /app/ffmpeg

# Copy application files
COPY . .

# Create the downloads directory
RUN mkdir -p downloads

EXPOSE 5000

CMD ["python", "app.py"]