"""Editar una sonda debe invalidar su propia evidencia.

El runner reutiliza los resultados en disco cuando la revisión coincide. Con
`+sucio` como único marcador, dos estados distintos del árbol dan la misma
cadena, y una sonda corregida devuelve en silencio los números de ANTES de la
corrección. Pasó de verdad con la sonda de tool-calling.
"""

from __future__ import annotations

from pathlib import Path

from edecan_forge_probe.__main__ import huella_de_las_sondas, revision_por_defecto


def test_la_huella_es_estable_entre_llamadas():
    assert huella_de_las_sondas() == huella_de_las_sondas()


def test_la_revision_incluye_la_huella():
    assert huella_de_las_sondas() in revision_por_defecto()


def test_editar_una_sonda_cambia_la_huella(tmp_path, monkeypatch):
    """Copia `probes/` a un temporal, toca un byte y comprueba que cambia."""
    import edecan_forge_probe.__main__ as cli

    origen = Path(cli.__file__).resolve().parent / "probes"
    destino = tmp_path / "paquete"
    (destino / "probes").mkdir(parents=True)
    for py in origen.glob("*.py"):
        (destino / "probes" / py.name).write_bytes(py.read_bytes())

    monkeypatch.setattr(cli, "__file__", str(destino / "__main__.py"))
    antes = huella_de_las_sondas()

    victima = next((destino / "probes").glob("*.py"))
    victima.write_bytes(victima.read_bytes() + b"\n# un byte de mas\n")
    despues = huella_de_las_sondas()

    assert antes != despues, "editar una sonda DEBE invalidar su evidencia"


def test_anadir_una_sonda_nueva_cambia_la_huella(tmp_path, monkeypatch):
    import edecan_forge_probe.__main__ as cli

    origen = Path(cli.__file__).resolve().parent / "probes"
    destino = tmp_path / "paquete"
    (destino / "probes").mkdir(parents=True)
    for py in origen.glob("*.py"):
        (destino / "probes" / py.name).write_bytes(py.read_bytes())

    monkeypatch.setattr(cli, "__file__", str(destino / "__main__.py"))
    antes = huella_de_las_sondas()
    (destino / "probes" / "zz_nueva.py").write_text("# sonda nueva\n", encoding="utf-8")
    assert huella_de_las_sondas() != antes
