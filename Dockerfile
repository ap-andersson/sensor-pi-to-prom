FROM python:3.12-slim-trixie

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PROMETHEUS_DISABLE_CREATED_SERIES=True

WORKDIR /app

COPY requirements.txt .
# --only-binary: fail loudly instead of trying to compile lgpio if a wheel is
# ever missing for the target platform.
RUN pip install --only-binary=:all: -r requirements.txt

COPY sensor_pi_to_prom ./sensor_pi_to_prom

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

EXPOSE 8000

CMD ["python3", "-m", "sensor_pi_to_prom"]
