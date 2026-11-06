"""Write data/report_template_<locale>.docx from the English data/report_template.docx.

Every paragraph (body, table cells, footer) whose text is in a locale's table is
replaced; formatting comes from paragraph styles, so the layout is kept. Run
again after editing the English template, from the module root:

    python -m tools.translate_report_template
"""

from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from deforestation_alerts.scripts.report_builder import _paragraphs

DATA = Path(__file__).resolve().parents[1] / "data"
FOOTER = "SEPAL · Deforestation alerts\t\tPage "

EN = [
    "Deforestation alert report",
    "Tree cover loss in {admin3}, {admin2}",
    "{admin1}  ·  Alert no. {group_alerts}  ·  Report of {confirmation_date}",
    "Area of loss",
    "Verdict",
    "First detection",
    "Alerts in this review",
    "Summary",
    "A loss of {area_loss} ha of tree cover was detected by {alert_system} in {admin3}, {admin2} ({admin1}), "
    "centred at {centroid} (latitude, longitude). The first detection is dated {detection_date1} and the last "
    "{detection_date2}. The alert was reviewed on {before_img} (before) and {after_img} (after) imagery, and "
    "classified as: {verdict}.",
    "Alert details",
    "Country",
    "Region",
    "District",
    "Alert system",
    "Last detection",
    "Latitude (centroid)",
    "Longitude (centroid)",
    "Before image",
    "After image",
    "Alert number(s)",
    "Report date",
    "Scan to open the centroid in Google Maps",
    "Satellite evidence",
    "Before · true colour",
    "After · true colour",
    "After · alert outline",
    "Alert outline in red",
    "Before · false colour",
    "After · false colour",
    "True colour: red, green, blue. False colour: near-infrared, red, green; forest shows dark red, "
    "cleared land and bare soil bright green or brown.",
    "Review and sign-off",
    "Field verification:   ☐ not needed     ☐ requested     ☐ done (date: ____________ )",
    "Reviewed by: ________________________________      Signature: ______________________",
    "Notes: _______________________________________________________________________________________",
    "About this report",
    "Alerts are clusters of connected alert pixels; the area is that of the cluster (of the whole group when "
    "nearby alerts were reviewed together). Coordinates are decimal degrees (WGS 84). Images keep the "
    "resolution of the finer of the two chosen scenes (Sentinel-2 10 m, Landsat 15 m, Planet 4.77 m); dates "
    "are those of the alert system's detections.",
    FOOTER,
]

LINE = "________________________________"


def notes(label):
    """``label`` and an underline that ends where the English one does (on one line)."""
    return label + " " + "_" * (85 - len(label))

TRANSLATIONS = {
    "es-ES": ("es-ES", [
        "Informe de alerta de deforestación",
        "Pérdida de cobertura arbórea en {admin3}, {admin2}",
        "{admin1}  ·  Alerta n.º {group_alerts}  ·  Informe del {confirmation_date}",
        "Superficie de pérdida",
        "Veredicto",
        "Primera detección",
        "Alertas en esta revisión",
        "Resumen",
        "{alert_system} detectó una pérdida de {area_loss} ha de cobertura arbórea en {admin3}, {admin2} "
        "({admin1}), con centro en {centroid} (latitud, longitud). La primera detección es del {detection_date1} "
        "y la última del {detection_date2}. La alerta se revisó con las imágenes {before_img} (antes) y "
        "{after_img} (después) y se clasificó como: {verdict}.",
        "Detalles de la alerta",
        "País",
        "Región",
        "Distrito",
        "Sistema de alertas",
        "Última detección",
        "Latitud (centroide)",
        "Longitud (centroide)",
        "Imagen de antes",
        "Imagen de después",
        "Número(s) de alerta",
        "Fecha del informe",
        "Escanee para abrir el centroide en Google Maps",
        "Evidencia satelital",
        "Antes · color verdadero",
        "Después · color verdadero",
        "Después · contorno",
        "Contorno de la alerta en rojo",
        "Antes · falso color",
        "Después · falso color",
        "Color verdadero: rojo, verde, azul. Falso color: infrarrojo cercano, rojo, verde; el bosque se ve rojo "
        "oscuro, la tierra desmontada y el suelo desnudo verde brillante o marrón.",
        "Revisión y firma",
        "Verificación en campo:   ☐ no necesaria     ☐ solicitada     ☐ realizada (fecha: ____________ )",
        f"Revisado por: {LINE}      Firma: ______________________",
        notes("Notas:"),
        "Acerca de este informe",
        "Las alertas son grupos de píxeles de alerta conectados; la superficie es la del grupo de píxeles (la de "
        "todo el grupo de alertas cuando se revisaron juntas alertas cercanas). Las coordenadas están en grados "
        "decimales (WGS 84). Las imágenes conservan la resolución de la más fina de las dos escenas elegidas "
        "(Sentinel-2 10 m, Landsat 15 m, Planet 4,77 m); las fechas son las de las detecciones del sistema de "
        "alertas.",
        "SEPAL · Alertas de deforestación\t\tPágina ",
    ]),
    "fr": ("fr-FR", [
        "Rapport d'alerte de déforestation",
        "Perte de couvert arboré à {admin3}, {admin2}",
        "{admin1}  ·  Alerte n° {group_alerts}  ·  Rapport du {confirmation_date}",
        "Surface perdue",
        "Verdict",
        "Première détection",
        "Alertes de cette révision",
        "Résumé",
        "{alert_system} a détecté une perte de {area_loss} ha de couvert arboré à {admin3}, {admin2} ({admin1}), "
        "centrée sur {centroid} (latitude, longitude). La première détection date du {detection_date1} et la "
        "dernière du {detection_date2}. L'alerte a été révisée sur les images {before_img} (avant) et "
        "{after_img} (après) et classée comme : {verdict}.",
        "Détails de l'alerte",
        "Pays",
        "Région",
        "District",
        "Système d'alerte",
        "Dernière détection",
        "Latitude (centroïde)",
        "Longitude (centroïde)",
        "Image avant",
        "Image après",
        "Numéro(s) d'alerte",
        "Date du rapport",
        "Scannez pour ouvrir le centroïde dans Google Maps",
        "Preuves satellitaires",
        "Avant · couleurs naturelles",
        "Après · couleurs naturelles",
        "Après · contour de l'alerte",
        "Contour de l'alerte en rouge",
        "Avant · fausses couleurs",
        "Après · fausses couleurs",
        "Couleurs naturelles : rouge, vert, bleu. Fausses couleurs : proche infrarouge, rouge, vert ; la forêt "
        "apparaît rouge foncé, les terres défrichées et le sol nu vert vif ou brun.",
        "Révision et validation",
        "Vérification de terrain :   ☐ non nécessaire     ☐ demandée     ☐ faite (date : ____________ )",
        f"Révisé par : {LINE}      Signature : ______________________",
        notes("Notes :"),
        "À propos de ce rapport",
        "Les alertes sont des amas de pixels d'alerte connectés ; la surface est celle de l'amas (de tout le "
        "groupe lorsque des alertes voisines ont été révisées ensemble). Les coordonnées sont en degrés décimaux "
        "(WGS 84). Les images gardent la résolution de la plus fine des deux scènes choisies (Sentinel-2 10 m, "
        "Landsat 15 m, Planet 4,77 m) ; les dates sont celles des détections du système d'alerte.",
        "SEPAL · Alertes de déforestation\t\tPage ",
    ]),
    "pt-BR": ("pt-BR", [
        "Relatório de alerta de desmatamento",
        "Perda de cobertura arbórea em {admin3}, {admin2}",
        "{admin1}  ·  Alerta nº {group_alerts}  ·  Relatório de {confirmation_date}",
        "Área de perda",
        "Veredito",
        "Primeira detecção",
        "Alertas nesta revisão",
        "Resumo",
        "{alert_system} detectou uma perda de {area_loss} ha de cobertura arbórea em {admin3}, {admin2} "
        "({admin1}), com centro em {centroid} (latitude, longitude). A primeira detecção é de {detection_date1} "
        "e a última de {detection_date2}. O alerta foi revisado com as imagens {before_img} (antes) e "
        "{after_img} (depois) e classificado como: {verdict}.",
        "Detalhes do alerta",
        "País",
        "Região",
        "Distrito",
        "Sistema de alerta",
        "Última detecção",
        "Latitude (centroide)",
        "Longitude (centroide)",
        "Imagem de antes",
        "Imagem de depois",
        "Número(s) do alerta",
        "Data do relatório",
        "Escaneie para abrir o centroide no Google Maps",
        "Evidências de satélite",
        "Antes · cor verdadeira",
        "Depois · cor verdadeira",
        "Depois · contorno do alerta",
        "Contorno do alerta em vermelho",
        "Antes · falsa cor",
        "Depois · falsa cor",
        "Cor verdadeira: vermelho, verde, azul. Falsa cor: infravermelho próximo, vermelho, verde; a floresta "
        "aparece em vermelho-escuro, a terra desmatada e o solo exposto em verde-claro ou marrom.",
        "Revisão e assinatura",
        "Verificação em campo:   ☐ não necessária     ☐ solicitada     ☐ feita (data: ____________ )",
        f"Revisado por: {LINE}      Assinatura: ______________________",
        notes("Observações:"),
        "Sobre este relatório",
        "Os alertas são agrupamentos de pixels de alerta conectados; a área é a do agrupamento (do grupo inteiro "
        "quando alertas próximos foram revisados juntos). As coordenadas estão em graus decimais (WGS 84). As "
        "imagens mantêm a resolução da mais fina das duas cenas escolhidas (Sentinel-2 10 m, Landsat 15 m, "
        "Planet 4,77 m); as datas são as das detecções do sistema de alerta.",
        "SEPAL · Alertas de desmatamento\t\tPágina ",
    ]),
}


def _set_language(doc, lang):
    """Proofing language of the Normal style (Word's spell checker)."""
    rpr = doc.styles["Normal"].element.get_or_add_rPr()
    el = rpr.find(qn("w:lang"))
    if el is None:
        el = OxmlElement("w:lang")
        rpr.append(el)
    el.set(qn("w:val"), lang)


def translate(locale: str) -> Path:
    lang, texts = TRANSLATIONS[locale]
    table = dict(zip(EN, texts, strict=True))
    doc = Document(DATA / "report_template.docx")
    found = set()
    for para in _paragraphs(doc):
        if para.text in table:
            found.add(para.text)
            para.text = table[para.text]
    footer = doc.sections[0].footer.paragraphs[0].runs[0]  # the page number field is in the next runs
    if footer.text in table:
        found.add(footer.text)
        footer.text = table[footer.text]
    missing = set(EN) - found
    assert not missing, f"not in the English template any more: {missing}"
    _set_language(doc, lang)
    out = DATA / f"report_template_{locale}.docx"
    doc.save(out)
    return out


if __name__ == "__main__":
    for locale in TRANSLATIONS:
        print(translate(locale))
