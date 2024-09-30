from sepal_ui import sepalwidgets as sw
from sepal_ui import aoi
from sepal_ui.mapping import SepalMap
from component.message import cm
import ipyvuetify as v
from sepal_ui.scripts.utils import init_ee
from traitlets import Any, Unicode, link

init_ee()


class ReviewTile(sw.Layout):
    def __init__(self, selected_alerts_model):

        self._metadata = {"mount_id": "review_tile"}
        self.selected_alerts_model = selected_alerts_model

        def calculateAlertClasses(gpdf):
            x = gpdf["review"] = 1
            y = gpdf["review"] = 2
            z = len(gpdf)
            result = list([x, y, z])
            return result

        self.listaNumeros = [150, 74, 65, 9]
        listaNumeros = self.listaNumeros
        super().__init__()

        # 1. Crear el mapa para seleccionar el área de estudio
        map_1 = SepalMap()
        map_1.layout.height = "100%"

        label61 = sw.Markdown("Total Alerts: " + str(listaNumeros[0]))
        label62 = sw.Markdown("Reviewed Alerts: " + str(listaNumeros[1]))
        label63 = sw.Markdown("Confirmed Alerts: " + str(listaNumeros[2]))
        label64 = sw.Markdown("False positives: " + str(listaNumeros[3]))

        # Layout 6 de la aplicación
        layout = sw.Row(
            style="height: 100vh;",
            children=[
                sw.Col(cols=9, style="height: 100vh;", children=[map_1]),
                sw.Col(
                    cols=3,
                    style="height: 100vh;",
                    children=[label61, label62, label63, label64],
                ),
            ],
        )
        self.children = [layout]
