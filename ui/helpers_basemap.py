"""Helpers para combo de mapa de fundo compartilhados entre widgets.

Permite reutilizar a lógica de Google Satélite / Esri Satélite / Planet
(login via diálogo) sem duplicar código em múltiplos widgets.
"""

from __future__ import annotations

from typing import Optional

from qgis.core import QgsProject, QgsRasterLayer
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .estilos import botao_style


XYZ_PERMITIDOS = ("Google Satélite", "Esri Satélite")
PLANET_ITEM_TEXT = "Planet (login necessário)"


# Credenciais persistentes durante a sessão do QGIS
_planet_last_email: str = ""
_planet_last_senha: str = ""


class BasemapManager:
    """Gerencia o combo de mapa de fundo e a aplicação no canvas."""

    def __init__(self, parent_widget: QWidget, canvas: QgsMapCanvas,
                 config: dict):
        self.parent = parent_widget
        self.canvas = canvas
        self.config = config
        self.camada_xyz: Optional[QgsRasterLayer] = None
        self.combo: Optional[QComboBox] = None

    def criar_combo(self) -> QComboBox:
        """Cria o QComboBox com as opções de mapa de fundo."""
        self.combo = QComboBox()
        idx_google = 0
        for nome, dados in self.config.get("wms_services", {}).items():
            if nome in XYZ_PERMITIDOS:
                self.combo.addItem(nome, dados.get("url", ""))
                if nome == "Google Satélite":
                    idx_google = self.combo.count() - 1
        self.combo.addItem(PLANET_ITEM_TEXT, "planet")
        self.combo.currentIndexChanged.connect(self._on_change)
        # Aplica Google Satélite como padrão
        if self.combo.currentIndex() == idx_google:
            self._on_change()
        else:
            self.combo.setCurrentIndex(idx_google)
        return self.combo

    def _on_change(self) -> None:
        if self.combo is None:
            return
        data = self.combo.currentData()
        if not data:
            return
        if data == "planet":
            self._abrir_planet_dialog()
            return
        nome = self.combo.currentText()
        layer = QgsRasterLayer(data, nome, "wms")
        if not layer.isValid():
            QMessageBox.warning(self.parent, "XYZ",
                                f"Não foi possível carregar {nome}.")
            return
        self.aplicar_camada_fundo(layer)

    def aplicar_camada_fundo(self, layer: QgsRasterLayer) -> None:
        """Substitui a camada de fundo (OSM ou anterior) pela nova."""
        QgsProject.instance().addMapLayer(layer, False)
        camadas = list(self.canvas.layers())
        if self.camada_xyz is not None and self.camada_xyz in camadas:
            camadas.remove(self.camada_xyz)
        osm = getattr(self.canvas, "_osm_layer", None)
        if osm is not None and osm in camadas:
            camadas.remove(osm)
        camadas.append(layer)
        self.canvas.setLayers(camadas)
        self.canvas.refresh()
        self.camada_xyz = layer

    def _voltar_combo_anterior(self) -> None:
        """Volta para a primeira opção válida (Google Satélite)."""
        if self.combo is None:
            return
        self.combo.blockSignals(True)
        for i in range(self.combo.count()):
            if self.combo.itemData(i) not in ("", "planet"):
                self.combo.setCurrentIndex(i)
                break
        self.combo.blockSignals(False)

    # ------------------------------------------------------------------ #
    #                          Planet                                    #
    # ------------------------------------------------------------------ #

    def _abrir_planet_dialog(self) -> None:
        global _planet_last_email, _planet_last_senha
        try:
            from ..core.planet_client import planet_client
        except ImportError:
            QMessageBox.warning(
                self.parent, "Planet",
                "Módulo 'requests' não disponível.\n"
                "Instale com: pip install requests"
            )
            self._voltar_combo_anterior()
            return

        dlg = QDialog(self.parent)
        dlg.setWindowTitle("Planet Basemaps")
        dlg.setMinimumWidth(440)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        gb_login = QGroupBox("Login Planet")
        fl = QFormLayout(gb_login)
        status_label = QLabel(
            "✓ Conectado" if planet_client.is_logged_in else "Desconectado"
        )
        status_label.setStyleSheet(
            "color: #27ae60; font-weight:bold;" if planet_client.is_logged_in
            else "color: #e74c3c; font-weight:bold;"
        )
        status_label.setAlignment(Qt.AlignCenter)
        fl.addRow(status_label)

        input_email = QLineEdit()
        input_email.setPlaceholderText("seu.email@dominio.com")
        input_email.setText(_planet_last_email)
        fl.addRow("Email:", input_email)

        input_senha = QLineEdit()
        input_senha.setEchoMode(QLineEdit.Password)
        input_senha.setPlaceholderText("Senha Planet")
        input_senha.setText(_planet_last_senha)
        fl.addRow("Senha:", input_senha)

        btn_login = QPushButton("Conectar")
        btn_login.setStyleSheet(botao_style("success"))
        fl.addRow(btn_login)
        layout.addWidget(gb_login)

        gb_mosaico = QGroupBox("Mosaicos Disponíveis")
        vm = QVBoxLayout(gb_mosaico)
        btn_listar = QPushButton("Listar mosaicos")
        btn_listar.setStyleSheet(botao_style("info"))
        btn_listar.setEnabled(planet_client.is_logged_in)
        vm.addWidget(btn_listar)

        lista = QListWidget()
        lista.setMinimumHeight(220)
        vm.addWidget(lista)

        btn_usar = QPushButton("Usar mosaico selecionado")
        btn_usar.setStyleSheet(botao_style("success"))
        btn_usar.setEnabled(False)
        vm.addWidget(btn_usar)
        layout.addWidget(gb_mosaico)

        cache: list = []

        def _fazer_login():
            global _planet_last_email, _planet_last_senha
            email = input_email.text().strip()
            senha = input_senha.text()
            if not email or not senha:
                QMessageBox.warning(dlg, "Login", "Informe email e senha.")
                return
            btn_login.setText("Conectando…")
            btn_login.setEnabled(False)
            from qgis.PyQt.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            ok, msg = planet_client.login(email, senha)
            btn_login.setText("Conectar")
            btn_login.setEnabled(True)
            if ok:
                _planet_last_email = email
                _planet_last_senha = senha
                status_label.setText("✓ Conectado")
                status_label.setStyleSheet("color: #27ae60; font-weight:bold;")
                btn_listar.setEnabled(True)
                self.combo.setItemText(
                    self.combo.findData("planet"), "Planet"
                )
                _listar()
            else:
                status_label.setText(f"✗ {msg}")
                status_label.setStyleSheet("color: #e74c3c; font-weight:bold;")

        def _listar():
            btn_listar.setText("Buscando…")
            btn_listar.setEnabled(False)
            from qgis.PyQt.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            mosaicos = planet_client.list_mosaics(
                name_contains="normalized_analytic"
            )
            cache.clear()
            cache.extend(mosaicos)
            lista.clear()
            for m in mosaicos:
                n = planet_client.get_mosaic_display_name(m)
                lista.addItem(QListWidgetItem(f"{n}  ({m.get('name', '')})"))
            btn_listar.setText("Listar mosaicos")
            btn_listar.setEnabled(True)
            if mosaicos:
                btn_usar.setEnabled(True)
            else:
                QMessageBox.information(dlg, "Planet", "Nenhum mosaico encontrado.")

        def _usar():
            idx = lista.currentRow()
            if idx < 0 or idx >= len(cache):
                return
            m = cache[idx]
            tile_url = planet_client.get_tile_url(m.get("name", ""))
            if not tile_url:
                return
            uri = f"type=xyz&url={tile_url}&zmax=19&zmin=0"
            n = planet_client.get_mosaic_display_name(m)
            layer = QgsRasterLayer(uri, f"Planet — {n}", "wms")
            if not layer.isValid():
                return
            self.aplicar_camada_fundo(layer)
            dlg.accept()

        btn_login.clicked.connect(_fazer_login)
        btn_listar.clicked.connect(_listar)
        btn_usar.clicked.connect(_usar)

        if planet_client.is_logged_in:
            _listar()

        if dlg.exec_() != QDialog.Accepted:
            self._voltar_combo_anterior()
