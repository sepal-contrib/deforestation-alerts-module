# Forest Cover Loss Alerts Analysis

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/SerafiniJose/defoAlerts/blob/master/LICENSE)
[![Black badge](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

Forest cover loss alerts analysis is a module built using the SEPAL UI framework and Solara, a Python-based tool for creating interactive web applications. It provides a comprehensive solution for monitoring and analyzing forest cover loss alerts across various geographic regions and timeframes. By integrating data from multiple sources such as GLAD-L and GLAD-S2 (Global Land Analysis & Discovery), RADD (RAdar for Detecting Deforestation), and CCDC (Continuous Change Detection and Classification), the platform allows users to access, visualize, and analyze forest cover loss hotspots. Additionally, it leverages a deep learning Convolutional Neural Network (CNN) model to refine and improve the accuracy of forest cover loss alerts. The platform also includes user-friendly reporting features that assist users in creating detailed reports and offering valuable information to address forest cover loss issues.

# About



# Development

## Tests

The runtime environment comes from `sepal_environment.yml` (micromamba/conda);
run the suite from the repository root in that environment:

```bash
micromamba create -y -n defo-alerts -f sepal_environment.yml   # once
micromamba activate defo-alerts
python -m pytest -q                       # the offline tier: no Earth Engine, no network
DEFO_ALERTS_EE_TESTS=1 python -m pytest -q  # everything, with the live Earth Engine tier
python -m pytest -q -m ee                 # the live Earth Engine tier only
```

The live Earth Engine tests (spec §13, Tier 2) are opt-in. Every test that
uses the `gee` fixture is marked `ee` (`tests/conftest.py`) and is skipped
unless `DEFO_ALERTS_EE_TESTS=1` is set or `-m ee` selects it; they need Earth
Engine credentials (`earthengine authenticate`) and only read from Earth
Engine. A test that is not marked `ee` and still calls Earth Engine fails.

## Gate walks

`tools/walk/` drives the app in a real browser for the per-slice gate walks
(`python -m tools.walk.slice6_walk --port 8986 --out /tmp/slice6_walk`). It
needs Playwright, which the runtime environment does not ship:

```bash
pip install playwright && playwright install chromium
```

The SEPAL gate itself (the app through SEPAL's proxy and the national-scale
measurement) is a manual runbook: `docs/sepal-gate.md`.
