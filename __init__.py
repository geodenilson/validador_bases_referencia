"""Validador de Bases de Referência - Plugin QGIS."""


def classFactory(iface):
    """Função obrigatória chamada pelo QGIS ao carregar o plugin."""
    from .plugin_main import ValidadorBasesReferenciaPlugin
    return ValidadorBasesReferenciaPlugin(iface)
