Módulo de análise de alertas é um aplicativo do SEPAL para monitorar alertas de perda de cobertura florestal em uma área de interesse, período após período.

Ele baixa alertas — GLAD-L e GLAD-S2 (Global Land Analysis & Discovery) e RADD (RAdar for Detecting Deforestation) do Google Earth Engine, e até seis fontes personalizadas de um asset do Earth Engine, de um GeoTIFF ou de um arquivo de pontos ou polígonos com data da sua pasta do SEPAL —, harmoniza-os em uma única grade no seu próprio disco e agrupa os pixels de alerta que se tocam em alertas, cada um listando todas as fontes que o detectaram.

Cada alerta pode então ser revisado em imagens ópticas e de radar de antes e depois, refinado com ferramentas de desenho e dois modelos de redes neurais convolucionais (CNN), e respondido de acordo com suas próprias perguntas, sozinho ou dividido entre vários avaliadores. A Visão geral resume todos os períodos, e os resultados são exportados como arquivos GeoPackage, tabelas CSV, relatórios Word a partir do seu modelo e animações GIF.

Seu trabalho é mantido em projetos, salvos automaticamente em `module_results/deforestation_alerts`. As receitas salvas com a versão anterior do aplicativo também abrem como projetos, para concluir e exportar suas revisões.
