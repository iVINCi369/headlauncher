FROM python:3.12-slim
ARG SENDME_VERSION=v0.36.0
ARG TARGETARCH
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends openssh-client ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
# docker CLI + compose plugin (talks to the host daemon through the mounted socket)
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:27-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
# sendme (iroh) for file transfer
RUN arch=$( [ "${TARGETARCH:-amd64}" = "arm64" ] && echo aarch64 || echo x86_64 ) \
    && curl -fsSL "https://github.com/n0-computer/sendme/releases/download/${SENDME_VERSION}/sendme-${SENDME_VERSION}-linux-${arch}.tar.gz" \
       | tar xz -C /usr/local/bin ./sendme && chmod +x /usr/local/bin/sendme
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
CMD ["python", "app.py"]
