"""Overview helpers: the alert class counts the Overview tile shows.

``calculate_alert_classes`` is a pure service (no widget code). The old marker
and popup builders that lived here were unused and are gone, and with them
this module's import of ``gui`` (the engine layer does not depend on the GUI).
"""


def calculate_alert_classes(
    gpdf,
    confirmed_label="Confirmed",
    false_positive_label="False Positive",
    maybe_label="Need revision",
):
    status_counts = gpdf["status"].value_counts().to_dict()

    total_alerts = len(gpdf)
    reviewed_alerts = sum(
        status_counts.get(status, 0)
        for status in [confirmed_label, false_positive_label, maybe_label]
    )
    confirmed_alerts = status_counts.get(confirmed_label, 0)
    false_positives = status_counts.get(false_positive_label, 0)
    need_revision = status_counts.get(maybe_label, 0)

    return [
        total_alerts,
        reviewed_alerts,
        confirmed_alerts,
        false_positives,
        need_revision,
    ]
