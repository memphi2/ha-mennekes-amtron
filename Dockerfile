FROM python:3.14-slim-bookworm@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527
WORKDIR /app
COPY pyproject.toml requirements-service.txt LICENSE NOTICE ./
COPY custom_components/mennekes_amtron_rtu/device ./custom_components/mennekes_amtron_rtu/device
RUN python -m pip install --no-cache-dir -r requirements-service.txt .
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8765
CMD ["amtron-modbus-service", "--config", "/data/options.json", "--state", "/data/intent.json"]
