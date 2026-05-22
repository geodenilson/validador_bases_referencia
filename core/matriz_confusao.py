"""Cálculo da matriz de confusão e índices de acurácia.

Implementa as métricas de validação de qualidade temática:
    * Exatidão Global (G)
    * Exatidão do Usuário (U) — complemento do erro de comissão
    * Exatidão do Produtor (P) — complemento do erro de omissão
    * Coeficiente Kappa (Cohen, 1960)
    * Kappa Condicional por classe
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from qgis.core import QgsVectorLayer


@dataclass
class ResultadoMatriz:
    classes: List[str]
    matriz: List[List[int]]
    exatidao_global: float
    kappa: float
    kappa_condicional: Dict[str, float]
    exatidao_usuario: Dict[str, float]
    exatidao_produtor: Dict[str, float]
    erro_comissao: Dict[str, float]
    erro_omissao: Dict[str, float]
    total_amostras: int
    total_diagonal: int
    parecer: Dict[str, object] = field(default_factory=dict)

    def linha_total(self) -> List[int]:
        return [sum(linha) for linha in self.matriz]

    def coluna_total(self) -> List[int]:
        return [sum(self.matriz[i][j] for i in range(len(self.matriz)))
                for j in range(len(self.classes))]


def _zerar_matriz(n: int) -> List[List[int]]:
    return [[0] * n for _ in range(n)]


def calcular_matriz_confusao(
    classes: List[str],
    pares: List[Tuple[str, str]],
) -> ResultadoMatriz:
    """Calcula matriz e índices a partir de pares (classificação, verdade).

    As colunas representam a verdade (referência) e as linhas a classificação,
    seguindo a convenção de Congalton & Green (1999).
    """
    indice = {c: i for i, c in enumerate(classes)}
    n = len(classes)
    matriz = _zerar_matriz(n)

    for classificacao, verdade in pares:
        if classificacao is None or verdade is None:
            continue
        if classificacao not in indice or verdade not in indice:
            continue
        i = indice[classificacao]
        j = indice[verdade]
        matriz[i][j] += 1

    total = sum(sum(linha) for linha in matriz)
    diagonal = sum(matriz[i][i] for i in range(n))

    exatidao_global = diagonal / total if total > 0 else 0.0

    # Kappa
    if total > 0:
        soma_linhas = [sum(matriz[i]) for i in range(n)]
        soma_colunas = [sum(matriz[i][j] for i in range(n)) for j in range(n)]
        pe = sum(
            (soma_linhas[i] * soma_colunas[i]) / (total * total)
            for i in range(n)
        )
        kappa = (exatidao_global - pe) / (1 - pe) if (1 - pe) > 0 else 0.0
    else:
        kappa = 0.0

    exatidao_usuario: Dict[str, float] = {}
    exatidao_produtor: Dict[str, float] = {}
    erro_comissao: Dict[str, float] = {}
    erro_omissao: Dict[str, float] = {}
    kappa_condicional: Dict[str, float] = {}

    for i, classe in enumerate(classes):
        soma_linha = sum(matriz[i])
        soma_coluna = sum(matriz[k][i] for k in range(n))
        nii = matriz[i][i]
        u = nii / soma_linha if soma_linha > 0 else 0.0
        p = nii / soma_coluna if soma_coluna > 0 else 0.0
        exatidao_usuario[classe] = u
        exatidao_produtor[classe] = p
        erro_comissao[classe] = 1 - u
        erro_omissao[classe] = 1 - p

        denom = total * soma_coluna - soma_linha * soma_coluna
        if denom != 0:
            kc = (total * nii - soma_linha * soma_coluna) / denom
        else:
            kc = 0.0
        kappa_condicional[classe] = kc

    return ResultadoMatriz(
        classes=classes,
        matriz=matriz,
        exatidao_global=exatidao_global,
        kappa=kappa,
        kappa_condicional=kappa_condicional,
        exatidao_usuario=exatidao_usuario,
        exatidao_produtor=exatidao_produtor,
        erro_comissao=erro_comissao,
        erro_omissao=erro_omissao,
        total_amostras=total,
        total_diagonal=diagonal,
    )


def calcular_a_partir_de_camada(
    camada: QgsVectorLayer,
    campo_classificacao: str,
    campo_verdade: str,
    classes: Optional[List[str]] = None,
) -> ResultadoMatriz:
    """Lê a camada e calcula a matriz de confusão.

    Se ``classes`` for None, descobre as classes presentes nos dois campos.
    """
    pares: List[Tuple[str, str]] = []
    presentes = set()
    for feat in camada.getFeatures():
        c = feat[campo_classificacao]
        v = feat[campo_verdade]
        if c is None or v is None or str(v).strip() == "":
            continue
        c, v = str(c), str(v)
        pares.append((c, v))
        presentes.add(c)
        presentes.add(v)
    if classes is None:
        classes = sorted(presentes, key=lambda x: str(x))
    return calcular_matriz_confusao(classes, pares)


def aplicar_parecer(
    resultado: ResultadoMatriz,
    kappa_minimo: float = 0.85,
    exatidao_global_minima: float = 0.85,
) -> ResultadoMatriz:
    """Adiciona dicionário com parecer ao resultado e o retorna."""
    aprovado_kappa = resultado.kappa >= kappa_minimo
    aprovado_global = resultado.exatidao_global >= exatidao_global_minima
    aprovado = aprovado_kappa and aprovado_global

    classificacao_kappa = classificar_kappa(resultado.kappa)

    resultado.parecer = {
        "aprovado": aprovado,
        "aprovado_kappa": aprovado_kappa,
        "aprovado_global": aprovado_global,
        "kappa_minimo": kappa_minimo,
        "exatidao_global_minima": exatidao_global_minima,
        "classificacao_kappa": classificacao_kappa,
    }
    return resultado


def classificar_kappa(kappa: float) -> str:
    """Retorna a classificação qualitativa do Kappa (Landis & Koch, 1977)."""
    if kappa < 0:
        return "Péssimo"
    if kappa <= 0.2:
        return "Ruim"
    if kappa <= 0.4:
        return "Razoável"
    if kappa <= 0.6:
        return "Bom"
    if kappa <= 0.8:
        return "Muito Bom"
    return "Excelente"


def exportar_csv(resultado: ResultadoMatriz, caminho: str) -> str:
    """Exporta a matriz e os índices para um arquivo CSV."""
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["MATRIZ DE CONFUSÃO"])
        writer.writerow(["Linhas = Classificação | Colunas = Verdade (Referência)"])
        writer.writerow([])
        cab = ["Classe (i)"] + list(resultado.classes) + ["Total linha (n_i+)", "U_Accuracy", "Erro Comissão"]
        writer.writerow(cab)
        for i, classe in enumerate(resultado.classes):
            linha = [classe] + list(resultado.matriz[i])
            soma_linha = sum(resultado.matriz[i])
            linha.append(soma_linha)
            linha.append(_pct(resultado.exatidao_usuario.get(classe)))
            linha.append(_pct(resultado.erro_comissao.get(classe)))
            writer.writerow(linha)
        soma_colunas = [sum(resultado.matriz[i][j] for i in range(len(resultado.classes)))
                        for j in range(len(resultado.classes))]
        writer.writerow(["Total coluna (n_+j)"] + soma_colunas + [resultado.total_amostras, "", ""])
        writer.writerow(["P_Accuracy"] +
                        [_pct(resultado.exatidao_produtor.get(c)) for c in resultado.classes] +
                        ["", "", ""])
        writer.writerow(["Erro Omissão"] +
                        [_pct(resultado.erro_omissao.get(c)) for c in resultado.classes] +
                        ["", "", ""])
        writer.writerow([])
        writer.writerow(["INDICES GLOBAIS"])
        writer.writerow(["Exatidão Global (G)", _pct(resultado.exatidao_global)])
        writer.writerow(["Kappa (K)", f"{resultado.kappa:.4f}"])
        writer.writerow(["Classificação Kappa", classificar_kappa(resultado.kappa)])
        writer.writerow(["Total de amostras", resultado.total_amostras])
        writer.writerow(["Total na diagonal", resultado.total_diagonal])
        writer.writerow([])
        writer.writerow(["KAPPA CONDICIONAL POR CLASSE"])
        writer.writerow(["Classe", "Kappa Condicional"])
        for classe in resultado.classes:
            writer.writerow([classe, f"{resultado.kappa_condicional.get(classe, 0):.4f}"])
        if resultado.parecer:
            writer.writerow([])
            writer.writerow(["PARECER"])
            writer.writerow(["Aprovado", "Sim" if resultado.parecer.get("aprovado") else "Não"])
            writer.writerow(["Kappa mínimo exigido", _pct(resultado.parecer.get("kappa_minimo"))])
            writer.writerow(["Exatidão global mínima", _pct(resultado.parecer.get("exatidao_global_minima"))])
    return caminho


def _pct(valor: Optional[float]) -> str:
    if valor is None:
        return ""
    return f"{valor * 100:.2f}%"
