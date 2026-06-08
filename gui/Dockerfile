# Deforestation Alerts — Solara deployment image
# Base: micromamba for fast conda env creation
FROM mambaorg/micromamba:1.5-jammy

# Copy environment spec and build the conda environment.
# These layers run before COPY . /app so source edits don't bust them.
COPY sepal_environment.yml /tmp/sepal_environment.yml

# NOTE (deployment follow-up): generate a pinned requirements.lock via
#   pip-compile or `pip freeze` after a successful build and switch the
#   COPY + pip install line below to use that lock file for fully
#   reproducible builds.
RUN micromamba create -y -n deforestation_alerts \
        -f /tmp/sepal_environment.yml \
    && micromamba clean --all --yes

# Install supervisor for process management (before COPY . /app for layer caching)
RUN micromamba run -n deforestation_alerts \
        pip install supervisor --quiet

# Copy the full repo into the image
COPY . /app
WORKDIR /app

# Place the supervisord config where supervisord expects it
COPY gui/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose the Solara port
EXPOSE 8900

# Run as root so supervisord's user=root directive in supervisord.conf is honored
USER root

# Launch supervisord (manages the solara process defined in supervisord.conf)
CMD ["micromamba", "run", "-n", "deforestation_alerts", \
     "supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
