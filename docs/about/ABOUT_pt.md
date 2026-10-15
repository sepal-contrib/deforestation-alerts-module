Forest Cover Loss Alerts é um aplicativo do SEPAL para monitorar alertas de perda de cobertura florestal em uma área de interesse, período após período.

Ele baixa alertas do Google Earth Engine — GLAD-L e GLAD-S2 (Global Land Analysis & Discovery), RADD (RAdar for Detecting Deforestation) e até seis fontes personalizadas, como os alertas CCDC do SEPAL —, harmoniza-os em uma única grade no seu próprio disco e agrupa os pixels de alerta que se tocam em alertas, cada um listando todas as fontes que o detectaram.

Cada alerta pode então ser revisado em imagens de satélite de antes e depois, refinado com ferramentas de desenho e dois modelos de redes neurais convolucionais (CNN), e respondido de acordo com suas próprias perguntas, sozinho ou dividido entre vários avaliadores. Os resultados são exportados como arquivos GeoPackage, relatórios Word a partir do seu modelo e animações GIF.

Seu trabalho é mantido em projetos, salvos automaticamente em `module_results/deforestation_alerts`.
