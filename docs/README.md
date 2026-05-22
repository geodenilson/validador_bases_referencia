# Validador de Bases de Referência — Plugin QGIS

Plugin para **validação de qualidade** e **adequação** de bases geográficas
de referência.

## Funcionalidades

### 1. Validação de Qualidade

| Sub-aba | O que faz |
|--------|-----------|
| **1.a Amostragem** | Calcula tamanho da amostra (Congalton & Green, 1999) e gera pontos amostrais (estratificada proporcional, igualitária, aleatória simples ou sistemática). |
| **1.b Rotulagem** | Mapa embutido para rotular cada ponto comparando classificação ↔ verdade observada (com Google Satélite ou Esri como fundo). |
| **1.c Matriz de Confusão & Parecer** | Calcula matriz, Exatidão Global, Kappa, Kappa Condicional, erros de comissão/omissão. Emite parecer (APROVADO/REPROVADO) conforme critérios configuráveis. Exporta CSV. |
| **1.d Quadrantes & PEC** | Gera quadrantes 1×1 km, sorteia amostras, ferramenta de medição em metros (vetor↔imagem), avalia PEC + omissão/comissão por quadrante, parecer PEC Classe A/B/C. |

### 2. Adequação de Bases

- Padronização conforme padrão escolhido (novos padrões serão adicionados no futuro).
- Separação de uso/cobertura em shapefiles individuais.
- Geração de bases unificadas com coluna `CLASSE` numérica.
- Reprojeção para SIRGAS 2000 (EPSG:4674).
- Reparo de geometrias (executado em **2 passes**).
- Subdivisão de polígonos > 500 vértices.
- Conversão linha/ponto → polígono (rio < 10 m / nascente, buffer 0,5 m).
- Remoção por hierarquia (Erase).

### 3. Geração de APP Hídrica

Implementa a Lei nº 12.651/2012:

| Categoria | APP | Classe |
|-----------|------|-------|
| Rio até 10 m | 30 m | 1 |
| Rio 10–50 m | 50 m | 2 |
| Rio 50–200 m | 100 m | 3 |
| Rio 200–600 m | 200 m | 4 |
| Rio > 600 m | 500 m | 5 |
| Lago/lagoa < 20 ha | 50 m | 6 |
| Lago/lagoa ≥ 20 ha | 100 m | 6 |
| Reservatório artificial | 30 m | 7 |
| Nascente | 50 m | 8 |

## Instalação

1. Copie a pasta `validador_bases_referencia/` para o diretório de plugins do
   QGIS:
   - **Windows:** `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - **Linux:** `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **macOS:** `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
2. Abra o QGIS → **Complementos → Gerenciar e Instalar Complementos**.
3. Marque **Validador de Bases de Referência**.
4. Use o ícone na barra ou o menu **Complementos → Validador de Bases de
   Referência**.

## Estrutura

```
validador_bases_referencia/
├── __init__.py
├── plugin_main.py
├── metadata.txt
├── icones/
│   └── logo.svg
├── config/
│   └── config.json
├── core/
│   ├── amostragem.py
│   ├── matriz_confusao.py
│   ├── adequacao_bases.py
│   ├── app_hidrica.py
│   └── utils.py
├── ui/
│   ├── main_window.py
│   ├── estilos.py
│   ├── tab_validacao.py
│   ├── tab_adequacao.py
│   ├── tab_app_hidrica.py
│   ├── widget_rotulagem.py
│   └── widget_quadrantes.py
└── docs/
    └── README.md
```

## Requisitos

- QGIS ≥ 3.16 (testado na linha 3.x).
- Algoritmos nativos do Processing (já incluídos no QGIS).
- Internet (opcional) para os mapas de fundo XYZ (Google/Esri).

## Referências

- Congalton & Green (1999). *Assessing the accuracy of remotely sensed data*.
- Landis & Koch (1977). *The measurement of observer agreement for categorical data*.
- Olofsson et al. (2014). *Good practices for estimating area and assessing accuracy of land change*.
- IBGE (2019). *Manual Técnico de Avaliação da Qualidade de Dados Geoespaciais*.
- Lei Federal nº 12.651/2012 (Código Florestal).
