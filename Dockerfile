FROM python:3.14-slim-bookworm@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527
WORKDIR /app
COPY pyproject.toml requirements-service.txt LICENSE NOTICE ./
COPY custom_components/mennekes_amtron_rtu/device ./custom_components/mennekes_amtron_rtu/device
RUN python -m pip install --no-cache-dir -r requirements-service.txt .
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# The service needs no root: its own user, in the group of serial adapters,
# owns the data directory of a fresh volume.
RUN groupadd --system --gid 10001 amtron \
    && useradd --system --uid 10001 --gid amtron --groups dialout --no-create-home --shell /usr/sbin/nologin amtron \
    && install --directory --owner amtron --group amtron --mode 0700 /data
USER 10001
EXPOSE 8765
CMD ["amtron-modbus-service", "--config", "/data/options.json", "--state", "/data/intent.json"]
