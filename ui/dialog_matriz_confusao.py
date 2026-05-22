"""Diálogo modal com a matriz de confusão dos pontos rotulados.

Exibe matriz no estilo Congalton & Green (1999), com índices Global e
Kappa, erros de omissão/comissão, exatidão do produtor/usuário e a
classificação qualitativa (Landis & Koch, 1977).

Campos fixos: ``classificacao`` (linhas) e ``verdade`` (colunas).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from qgis.core import QgsVectorLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import matriz_confusao
from .estilos import botao_style


CAMPO_CLASSIFICACAO = "classificacao"
CAMPO_VERDADE = "verdade"

# Valores tratados como "vazio" (não rotulado) — ignorados na matriz
VALORES_NULOS = {"", "null", "none", "nan", "n/a", "na"}


def _eh_nulo(valor) -> bool:
    if valor is None:
        return True
    s = str(valor).strip().lower()
    return s in VALORES_NULOS


def _ler_pares_validos(
    camada: QgsVectorLayer,
    campo_class: str,
    campo_verd: str,
) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Lê a camada e retorna pares (classificacao, verdade) válidos +
    lista de classes únicas (excluindo NULL/empty)."""
    pares: List[Tuple[str, str]] = []
    presentes = set()
    for feat in camada.getFeatures():
        c = feat[campo_class]
        v = feat[campo_verd]
        if _eh_nulo(c) or _eh_nulo(v):
            continue
        c_s, v_s = str(c).strip(), str(v).strip()
        pares.append((c_s, v_s))
        presentes.add(c_s)
        presentes.add(v_s)
    classes = sorted(presentes)
    return pares, classes


def _cor_classificacao(valor: float) -> str:
    """Cor segundo a régua de Landis & Koch (sobre 0-1)."""
    if valor < 0.20:
        return "#c0392b"
    if valor < 0.40:
        return "#e67e22"
    if valor < 0.60:
        return "#f39c12"
    if valor < 0.80:
        return "#27ae60"
    return "#16a085"


class DialogMatrizConfusao(QDialog):

    def __init__(self, parent, camada_pontos: QgsVectorLayer):
        super().__init__(parent)
        self.setWindowTitle("Matriz de Confusão")
        self.setMinimumSize(820, 540)

        self.camada = camada_pontos
        self.resultado: Optional[matriz_confusao.ResultadoMatriz] = None

        self._build_ui()
        self._calcular()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # --- Tabela da matriz ---
        self.tbl = QTableWidget()
        self.tbl.setStyleSheet(
            """
            QTableWidget {
                gridline-color: #b0b6bb;
                background-color: #ffffff;
                font-size: 12px;
            }
            QTableWidget::item {
                padding: 4px;
            }
            """
        )
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.horizontalHeader().setVisible(False)
        self.tbl.setShowGrid(True)
        self.tbl.setSelectionMode(QTableWidget.NoSelection)
        self.tbl.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.tbl, 1)

        # --- Botões ---
        h = QHBoxLayout()
        btn_csv = QPushButton("💾 Salvar CSV")
        btn_csv.setStyleSheet(botao_style("info"))
        btn_csv.clicked.connect(self._salvar_csv)
        btn_fechar = QPushButton("Fechar")
        btn_fechar.setStyleSheet(botao_style("secondary"))
        btn_fechar.clicked.connect(self.accept)
        h.addWidget(btn_csv)
        h.addStretch()
        h.addWidget(btn_fechar)
        layout.addLayout(h)

    # ------------------------------------------------------------------ #
    #                          Cálculo                                   #
    # ------------------------------------------------------------------ #

    def _calcular(self) -> None:
        nomes_campos = [f.name() for f in self.camada.fields()]
        if CAMPO_CLASSIFICACAO not in nomes_campos:
            QMessageBox.warning(
                self, "Campo ausente",
                f"A camada não tem o campo '{CAMPO_CLASSIFICACAO}'."
            )
            return
        if CAMPO_VERDADE not in nomes_campos:
            QMessageBox.warning(
                self, "Campo ausente",
                f"A camada não tem o campo '{CAMPO_VERDADE}'.\n"
                "Rotule pelo menos alguns pontos antes de abrir a matriz."
            )
            return

        pares, classes = _ler_pares_validos(
            self.camada, CAMPO_CLASSIFICACAO, CAMPO_VERDADE
        )
        if not pares or not classes:
            QMessageBox.information(
                self, "Sem dados",
                "Nenhum ponto rotulado foi encontrado.\n"
                "Rotule alguns pontos e tente novamente."
            )
            return

        r = matriz_confusao.calcular_matriz_confusao(classes, pares)
        self.resultado = r
        self._renderizar(r)

    # ------------------------------------------------------------------ #
    #                          Renderização                              #
    # ------------------------------------------------------------------ #

    def _renderizar(self, r: matriz_confusao.ResultadoMatriz) -> None:
        n = len(r.classes)
        letras = [chr(ord("a") + i) for i in range(n)]

        # Cores
        COR_CAB = QColor("#1a5276")         # azul escuro do cabeçalho
        COR_CAB_TXT = QColor("#ffffff")     # branco no cabeçalho
        COR_CLASSE_BG = QColor("#eaf2f8")   # azul muito claro p/ rótulos
        COR_DIAG = QColor("#7a8a99")        # cinza azulado (diagonal)
        COR_DIAG_TXT = QColor("#ffffff")    # diagonal: texto branco
        COR_CELULA = QColor("#ffffff")
        COR_RESUMO = QColor("#f4f6f8")      # cinza muito claro (resumo)
        COR_INDICE_HDR = QColor("#1a5276")  # cabeçalho do bloco de Índice

        # ----- Layout do quadro -----
        #   linhas:  1 cabeçalho + n classes + 4 (Total, Ti, Eo/Ec, P/U)
        #   colunas: 1 cabeçalho + n classes + 4 (T, Ti, Ec, U)
        rows = n + 5
        cols = n + 5
        self.tbl.clear()
        self.tbl.setRowCount(rows)
        self.tbl.setColumnCount(cols)

        def _item(texto, bold=False, color="#1a5276", bg=None,
                  align=Qt.AlignCenter, size=12) -> QTableWidgetItem:
            it = QTableWidgetItem(str(texto))
            it.setTextAlignment(align)
            if bg is not None:
                it.setBackground(bg)
            font = QFont()
            font.setPointSize(size)
            font.setBold(bold)
            it.setFont(font)
            if isinstance(color, QColor):
                it.setForeground(color)
            else:
                it.setForeground(QColor(color))
            it.setFlags(Qt.ItemIsEnabled)
            return it

        # ===== Cabeçalho superior (azul, branco, negrito) =====
        self.tbl.setItem(0, 0, _item(
            "Classificação ↓ / Verdade →", bold=True,
            color=COR_CAB_TXT, bg=COR_CAB, size=12
        ))
        for j, l in enumerate(letras):
            self.tbl.setItem(0, 1 + j, _item(
                l, bold=True, color=COR_CAB_TXT, bg=COR_CAB, size=13
            ))
        for j, lbl in enumerate(["T", "Ti", "Ec", "U"]):
            self.tbl.setItem(0, 1 + n + j, _item(
                lbl, bold=True, color=COR_CAB_TXT, bg=COR_CAB, size=13
            ))
        self.tbl.setRowHeight(0, 30)

        # ===== Linhas das classes =====
        soma_colunas = r.coluna_total()
        for i, classe in enumerate(r.classes):
            row = 1 + i
            # Rótulo da classe (esquerda)
            self.tbl.setItem(row, 0, _item(
                f"{classe} ({letras[i]})", bold=True,
                color="#1a5276", bg=COR_CLASSE_BG,
                align=Qt.AlignCenter, size=12
            ))
            soma_linha = sum(r.matriz[i])
            ti = soma_linha - r.matriz[i][i]
            for j in range(n):
                v = r.matriz[i][j]
                if i == j:
                    self.tbl.setItem(row, 1 + j, _item(
                        v, bold=True, color=COR_DIAG_TXT,
                        bg=COR_DIAG, size=13
                    ))
                else:
                    self.tbl.setItem(row, 1 + j, _item(
                        v, color="#222", bg=COR_CELULA, size=12
                    ))
            self.tbl.setItem(row, 1 + n, _item(
                soma_linha, bold=True, color="#1a5276", size=12
            ))
            self.tbl.setItem(row, 2 + n, _item(
                ti, bold=True, color="#1a5276", size=12
            ))
            ec = r.erro_comissao.get(classe, 0) * 100
            u = r.exatidao_usuario.get(classe, 0) * 100
            self.tbl.setItem(row, 3 + n, _item(
                f"{ec:.1f}%", bold=True,
                color=_cor_classificacao(1 - r.erro_comissao.get(classe, 0)),
                size=12
            ))
            self.tbl.setItem(row, 4 + n, _item(
                f"{u:.1f}%", bold=True,
                color=_cor_classificacao(r.exatidao_usuario.get(classe, 0)),
                size=12
            ))

        # ===== Linhas resumo =====
        row_total = 1 + n
        row_ti = 2 + n
        row_eo = 3 + n
        row_pu = 4 + n

        self.tbl.setItem(row_total, 0, _item(
            "Total (T)", bold=True, color="#1a5276", bg=COR_RESUMO,
            align=Qt.AlignCenter, size=12
        ))
        self.tbl.setItem(row_ti, 0, _item(
            "Total omitido (Ti)", bold=True, color="#1a5276", bg=COR_RESUMO,
            align=Qt.AlignCenter, size=12
        ))
        self.tbl.setItem(row_eo, 0, _item(
            "Erro de omissão (Eo) %", bold=True, color="#1a5276",
            bg=COR_RESUMO, align=Qt.AlignCenter, size=12
        ))
        self.tbl.setItem(row_pu, 0, _item(
            "Exatidão do produtor (P) %", bold=True, color="#1a5276",
            bg=COR_RESUMO, align=Qt.AlignCenter, size=12
        ))

        for j in range(n):
            self.tbl.setItem(row_total, 1 + j, _item(
                soma_colunas[j], bold=True, color="#1a5276",
                bg=COR_RESUMO, size=12
            ))
            to = soma_colunas[j] - r.matriz[j][j]
            self.tbl.setItem(row_ti, 1 + j, _item(
                to, bold=True, color="#1a5276", bg=COR_RESUMO, size=12
            ))
            classe = r.classes[j]
            eo = r.erro_omissao.get(classe, 0) * 100
            self.tbl.setItem(row_eo, 1 + j, _item(
                f"{eo:.1f}%", bold=True,
                color=_cor_classificacao(1 - r.erro_omissao.get(classe, 0)),
                bg=COR_RESUMO, size=12
            ))
            p = r.exatidao_produtor.get(classe, 0) * 100
            self.tbl.setItem(row_pu, 1 + j, _item(
                f"{p:.1f}%", bold=True,
                color=_cor_classificacao(r.exatidao_produtor.get(classe, 0)),
                bg=COR_RESUMO, size=12
            ))

        # ===== Bloco lateral direito (Global / Kappa / Landis & Koch) =====
        # Linha 1+n: cabeçalho "Índice" (mesclado nas 4 colunas finais)
        self.tbl.setSpan(row_total, 1 + n, 1, 4)
        self.tbl.setItem(row_total, 1 + n, _item(
            "Índice de Acurácia", bold=True,
            color=COR_CAB_TXT, bg=COR_INDICE_HDR, size=12
        ))

        # Linha Global: label | valor | classificação (2 cols mescladas)
        self.tbl.setItem(row_ti, 1 + n, _item(
            "Global", bold=True, color="#1a5276",
            bg=COR_RESUMO, size=12
        ))
        self.tbl.setItem(row_ti, 2 + n, _item(
            f"{r.exatidao_global * 100:.2f}%", bold=True,
            color=_cor_classificacao(r.exatidao_global),
            bg=COR_RESUMO, size=13
        ))
        self.tbl.setSpan(row_ti, 3 + n, 1, 2)
        self.tbl.setItem(row_ti, 3 + n, _item(
            matriz_confusao.classificar_kappa(r.exatidao_global), bold=True,
            color=_cor_classificacao(r.exatidao_global),
            bg=COR_RESUMO, size=12
        ))

        # Linha Kappa: label | valor | classificação (2 cols mescladas)
        self.tbl.setItem(row_eo, 1 + n, _item(
            "Kappa", bold=True, color="#1a5276",
            bg=COR_RESUMO, size=12
        ))
        self.tbl.setItem(row_eo, 2 + n, _item(
            f"{r.kappa * 100:.2f}%", bold=True,
            color=_cor_classificacao(r.kappa),
            bg=COR_RESUMO, size=13
        ))
        self.tbl.setSpan(row_eo, 3 + n, 1, 2)
        self.tbl.setItem(row_eo, 3 + n, _item(
            matriz_confusao.classificar_kappa(r.kappa), bold=True,
            color=_cor_classificacao(r.kappa),
            bg=COR_RESUMO, size=12
        ))

        # Rodapé descritivo
        self.tbl.setSpan(row_pu, 1 + n, 1, 4)
        self.tbl.setItem(row_pu, 1 + n, _item(
            "Landis & Koch (1977)", color="#666",
            bg=COR_RESUMO, size=10
        ))

        # ===== Ajustes finais de largura/altura =====
        self.tbl.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.tbl.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        # Garante largura mínima na primeira coluna (rótulos longos)
        self.tbl.setColumnWidth(0, max(220, self.tbl.columnWidth(0)))

    # ------------------------------------------------------------------ #
    #                          Exportação                                #
    # ------------------------------------------------------------------ #

    def _salvar_csv(self) -> None:
        if self.resultado is None:
            QMessageBox.warning(self, "Sem dados", "Calcule a matriz primeiro.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Salvar matriz como CSV", "matriz_confusao.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            matriz_confusao.exportar_csv(self.resultado, path)
            QMessageBox.information(self, "Exportado", f"Arquivo salvo em:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Erro", str(exc))
