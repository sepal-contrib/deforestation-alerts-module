# Deforestation Alerts — Solara deployment image
# Base: micromamba for fast conda env creation
FROM mambaorg/micromamba:1.5-jammy

# Copy environment spec and build the conda environment.
# These layers run before COPY . /app so source edits don't bust them.
COPY sepal_environment.yml /tmp/sepal_environment.yml

# git is a build-time tool only (pip needs it to clone the earthengine-api
# fork pinned via git+https:// in sepal_environment.yml) — it is not an
# application dependency, so it is installed here rather than added to the
# YAML, which stays the app's dependency authority.
# Installed via apt (not `micromamba install -n base`): micromamba's internal
# pip invocation for the env's `pip:` block runs with a PATH that does not
# include base's bin dir, so a conda-installed git in base is invisible to it;
# apt puts git on the system PATH (/usr/bin), which every env sees regardless
# of which conda env is activated.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
USER $MAMBA_USER

# NOTE: sepal_environment.yml is the pinned lock — every conda and pip
#   dependency below is version-pinned in that file, so no separate
#   requirements.lock generation step is needed.
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
