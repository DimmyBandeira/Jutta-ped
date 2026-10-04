from __future__ import annotations

from pathlib import Path

from src.jutta_ped.service.placement import pt_irmao, resolve_placement


def _modelos(tmp_path: Path, com_pt: bool = True) -> tuple[Path, Path]:
    pessoa = tmp_path / "pessoa_openvino_model"
    espec = tmp_path / "espec_openvino_model"
    pessoa.mkdir()
    espec.mkdir()
    if com_pt:
        (tmp_path / "pessoa.pt").write_bytes(b"x")
        (tmp_path / "espec.pt").write_bytes(b"x")
    return pessoa, espec


def _cuda(ok: bool):
    return lambda: (ok, "cuda_disponivel" if ok else "torch_sem_cuda_count_0")


def test_auto_com_openvino_declara_cpu_mesmo_com_cuda(tmp_path: Path) -> None:
    pessoa, espec = _modelos(tmp_path)
    p = resolve_placement(force_cpu=False, requested="auto", person_model_path=pessoa, specialist_model_path=espec, cuda_probe=_cuda(True))
    assert (p.device, p.backend, p.reason) == ("cpu", "openvino", "openvino_cpu")
    assert p.person_model_path == pessoa


def test_cuda_explicito_troca_para_pt_irmao(tmp_path: Path) -> None:
    pessoa, espec = _modelos(tmp_path)
    p = resolve_placement(force_cpu=False, requested="cuda", person_model_path=pessoa, specialist_model_path=espec, cuda_probe=_cuda(True))
    assert (p.device, p.backend) == ("cuda:0", "pytorch")
    assert p.person_model_path == tmp_path / "pessoa.pt"
    assert p.specialist_model_path == tmp_path / "espec.pt"


def test_cuda_explicito_sem_cuda_cai_para_cpu_com_motivo(tmp_path: Path) -> None:
    pessoa, espec = _modelos(tmp_path)
    p = resolve_placement(force_cpu=False, requested="cuda:0", person_model_path=pessoa, specialist_model_path=espec, cuda_probe=_cuda(False))
    assert p.device == "cpu" and p.backend == "openvino"
    assert p.reason.startswith("cuda_indisponivel_fallback_cpu")
    assert p.person_model_path == pessoa


def test_cuda_explicito_sem_pesos_pt_cai_para_cpu(tmp_path: Path) -> None:
    pessoa, espec = _modelos(tmp_path, com_pt=False)
    p = resolve_placement(force_cpu=False, requested="cuda", person_model_path=pessoa, specialist_model_path=espec, cuda_probe=_cuda(True))
    assert (p.device, p.reason) == ("cpu", "cuda_sem_pesos_pt_fallback_cpu")


def test_force_cpu_vence_pedido_cuda(tmp_path: Path) -> None:
    pessoa, espec = _modelos(tmp_path)
    p = resolve_placement(force_cpu=True, requested="cuda", person_model_path=pessoa, specialist_model_path=espec, cuda_probe=_cuda(True))
    assert (p.device, p.reason) == ("cpu", "force_cpu")


def test_auto_com_pt_usa_cuda_quando_existe(tmp_path: Path) -> None:
    pessoa, espec = tmp_path / "a.pt", tmp_path / "b.pt"
    pessoa.write_bytes(b"x")
    espec.write_bytes(b"x")
    com = resolve_placement(force_cpu=False, requested="auto", person_model_path=pessoa, specialist_model_path=espec, cuda_probe=_cuda(True))
    sem = resolve_placement(force_cpu=False, requested="auto", person_model_path=pessoa, specialist_model_path=espec, cuda_probe=_cuda(False))
    assert (com.device, com.backend) == ("cuda:0", "pytorch")
    assert (sem.device, sem.backend) == ("cpu", "pytorch")


def test_pedido_manual_intel_gpu_e_respeitado(tmp_path: Path) -> None:
    pessoa, espec = _modelos(tmp_path)
    p = resolve_placement(force_cpu=False, requested="intel:gpu", person_model_path=pessoa, specialist_model_path=espec, cuda_probe=_cuda(True))
    assert (p.device, p.backend, p.reason) == ("intel:gpu", "openvino", "manual")


def test_pt_irmao_inexistente_devolve_none(tmp_path: Path) -> None:
    pasta = tmp_path / "x_openvino_model"
    pasta.mkdir()
    assert pt_irmao(pasta) is None
