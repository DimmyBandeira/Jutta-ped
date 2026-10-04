"""Politica de colocacao da inferencia: em que dispositivo cada sessao realmente roda.

Antes, `resolve_device("auto")` devolvia `cuda:0` sempre que o torch via CUDA, mas os modelos padrao sao
OpenVINO e rodam na CPU: o relatorio dizia GPU, e o perfil economico de CPU (stride/teto do especialista)
nao era aplicado. Aqui o dispositivo declarado passa a ser o que de fato executa:

* modelo OpenVINO (`*_openvino_model/`) -> CPU (ou `intel:gpu` quando pedido explicitamente);
* modelo PyTorch (`.pt`) -> `cuda:N` se houver CUDA, senao CPU;
* pedido explicito de CUDA troca os modelos OpenVINO pelo `.pt` irmao; sem CUDA ou sem `.pt`, cai para
  a CPU com o motivo registrado (nunca falha a sessao por isso).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_SUFIXO_OV = "_openvino_model"


@dataclass(frozen=True)
class Placement:
    device: str
    person_model_path: Path
    specialist_model_path: Path
    backend: str            # "openvino" | "pytorch"
    reason: str


def cuda_disponivel() -> tuple[bool, str]:
    try:
        import torch

        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            return True, "cuda_disponivel"
        return False, f"torch_sem_cuda_count_{torch.cuda.device_count()}"
    except Exception as exc:  # noqa: BLE001 -- torch ausente/quebrado nao pode derrubar a sessao
        return False, f"torch_indisponivel_{type(exc).__name__}"


def _is_openvino(path: Path) -> bool:
    return path.is_dir() or path.suffix.lower() == ".xml"


def pt_irmao(path: Path) -> Path | None:
    """`x_openvino_model/` -> `x.pt` (mesma pasta), se existir."""
    if path.is_dir() and path.name.endswith(_SUFIXO_OV):
        candidato = path.with_name(path.name[: -len(_SUFIXO_OV)] + ".pt")
        return candidato if candidato.is_file() else None
    return None


def resolve_placement(
    *,
    force_cpu: bool,
    requested: str,
    person_model_path: Path,
    specialist_model_path: Path,
    cuda_probe=cuda_disponivel,
) -> Placement:
    pessoa, espec = Path(person_model_path), Path(specialist_model_path)
    pedido = str(requested or "auto").strip().lower()
    ov = _is_openvino(pessoa) and _is_openvino(espec)

    if force_cpu:
        return Placement("cpu", pessoa, espec, "openvino" if ov else "pytorch", "force_cpu")

    if pedido.startswith("cuda"):
        ok, motivo = cuda_probe()
        if not ok:
            return Placement("cpu", pessoa, espec, "openvino" if ov else "pytorch", f"cuda_indisponivel_fallback_cpu:{motivo}")
        novo_pessoa = pt_irmao(pessoa) if _is_openvino(pessoa) else pessoa
        novo_espec = pt_irmao(espec) if _is_openvino(espec) else espec
        if novo_pessoa is None or novo_espec is None:
            return Placement("cpu", pessoa, espec, "openvino" if ov else "pytorch", "cuda_sem_pesos_pt_fallback_cpu")
        return Placement(pedido if ":" in pedido else "cuda:0", novo_pessoa, novo_espec, "pytorch", "cuda_solicitado")

    if pedido not in {"", "auto"}:
        # intel:gpu, cpu, etc.: respeita o pedido; o backend segue o formato dos modelos.
        return Placement(pedido, pessoa, espec, "openvino" if ov else "pytorch", "manual")

    # auto: o dispositivo declarado e o do backend dos modelos.
    if ov:
        return Placement("cpu", pessoa, espec, "openvino", "openvino_cpu")
    ok, motivo = cuda_probe()
    return Placement("cuda:0" if ok else "cpu", pessoa, espec, "pytorch", motivo)
