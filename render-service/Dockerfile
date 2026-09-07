FROM node:18-bookworm-slim

# Install Chromium and FFmpeg for Remotion rendering + emoji fonts
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        chromium \
        ffmpeg \
        fonts-noto-color-emoji \
        fonts-symbola \
        fontconfig \
        ca-certificates \
        curl \
        unzip && \
    rm -rf /var/lib/apt/lists/* && \
    fc-cache -f

ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV REMOTION_BUNDLE_PATH=/app/remotion
ENV OUTPUT_DIR=/output

WORKDIR /app

# ---- Render service ----
COPY render-service/package.json render-service/package-lock.json ./
RUN npm ci

COPY render-service/tsconfig.json ./
COPY render-service/src/ ./src/

# ---- Remotion project ----
COPY remotion/package.json /app/remotion/package.json
COPY remotion/package-lock.json /app/remotion/package-lock.json
COPY remotion/tsconfig.json /app/remotion/tsconfig.json
COPY remotion/src/ /app/remotion/src/
COPY remotion/public/ /app/remotion/public/

# Install exactly the locked Remotion dependency tree
RUN cd /app/remotion && npm ci --legacy-peer-deps

# Fetch Google Animated Emoji assets
RUN mkdir -p /tmp/animated && \
    curl -L https://github.com/remotion-dev/animated-emoji/archive/refs/heads/main.zip \
      -o /tmp/animated.zip && \
    unzip -q /tmp/animated.zip -d /tmp && \
    cp -r /tmp/animated-emoji-main/public/* /app/remotion/public/ 2>/dev/null || true && \
    rm -rf /tmp/animated /tmp/animated.zip && \
    ls /app/remotion/public | wc -l && \
    du -sh /app/remotion/public

# Build render service
RUN npm run build

EXPOSE 3100
CMD ["node", "dist/server.js"]