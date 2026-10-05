import pytest
import yaml
from sqlalchemy import select

from painel_helpers import _login, _hx
from test_painel_cameras_go2rtc import ambiente
from src.jutta_ped.painel.models import Camera, Usuario, Papel
from src.jutta_ped.painel.security import hash_senha
from src.jutta_ped.painel.simulater_dsa import ler_cenario


def documento(**extra):
    d = {"version": 1, "name": "Teste", "server": {"rtsp_port": 8554},
         "sources": [{"enabled": True, "path": "C:/nao-abrir.mp4", "cameras": ["teste_01", "teste_02"]},
                     {"enabled": False, "cameras": ["desligada"]}]}
    d.update(extra)
    return yaml.safe_dump(d).encode()


def importar(a, conteudo=None):
    return _hx(a.client, "POST", "/painel/cameras/simulater-dsa", data={"host": "127.0.0.1"},
               files={"arquivo_yaml": ("cenario.yaml", documento() if conteudo is None else conteudo, "application/yaml")})


def test_importacao_sincroniza_e_repeticao_preserva(ambiente):
    a = ambiente
    _login(a.client)
    assert "SimulaterDSA" in a.client.get("/painel/cameras").text
    assert 'multipart/form-data' in a.client.get("/painel/cameras/simulater-dsa").text
    r = importar(a)
    assert r.status_code == 200 and "2 câmeras cadastradas" in r.text
    assert set(a.go2rtc.streams.values()) == {"rtsp://127.0.0.1:8554/teste_01", "rtsp://127.0.0.1:8554/teste_02"}
    assert "0 câmeras cadastradas" in importar(a).text
    with a.database.session() as db:
        cameras = list(db.scalars(select(Camera)))
        assert len(cameras) == 2
        assert all(not c.ia_pediatria for c in cameras)
        assert all(c.go2rtc_sync == "ok" for c in cameras)


def test_conflito_nao_importa_parcialmente(ambiente):
    a = ambiente
    _login(a.client)
    importar(a)
    d = yaml.safe_load(documento())
    d["server"]["rtsp_port"] = 9999
    d["sources"][0]["cameras"] = ["nova", "teste_01"]
    assert "Nenhuma foi importada" in importar(a, yaml.safe_dump(d).encode()).text
    with a.database.session() as db:
        assert len(list(db.scalars(select(Camera)))) == 2


@pytest.mark.parametrize("conteudo", [b"streams: {}", b"[", documento(version=True), documento(server={"rtsp_port": True}),
    documento(sources=[{"cameras": ["../escape"]}]), documento(sources=[{"cameras": ["a", "A"]}]),
    documento(sources=[{"enabled": "false", "cameras": ["a"]}]), documento(sources=[]), b"x" * 1_000_001],
    ids=["go2rtc", "yaml", "versao", "porta", "caminho", "duplicada", "enabled", "vazio", "limite"])
def test_cenario_invalido(conteudo):
    with pytest.raises(ValueError):
        ler_cenario(conteudo, "127.0.0.1")


def test_host_e_porta_do_cenario():
    assert ler_cenario(documento(server={"rtsp_port": 9554}), "servidor.local")[0][1] == "rtsp://servidor.local:9554/teste_01"
    with pytest.raises(ValueError):
        ler_cenario(documento(), "host:8554/extra")


def test_csrf_obrigatorio(ambiente):
    _login(ambiente.client)
    r = ambiente.client.post("/painel/cameras/simulater-dsa", files={"arquivo_yaml": ("c.yaml", documento())})
    assert r.status_code == 403


def test_importacao_restrita_a_admin(ambiente):
    a = ambiente
    with a.database.session() as db:
        db.add(Usuario(login="comum-sim", senha_hash=hash_senha("senha-comum-123"), papel=Papel.comum, ativo=True))
    _login(a.client, "comum-sim", "senha-comum-123")
    assert "SimulaterDSA — importar" not in a.client.get("/painel/cameras").text
    assert a.client.get("/painel/cameras/simulater-dsa").status_code == 403
    assert importar(a).status_code == 403
