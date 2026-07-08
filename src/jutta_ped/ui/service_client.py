"""Unico ponto por onde a UI (Demo Viewer) toca a camada de servico.

Regra de dependencia do plugin: `ui -> service -> core`, nunca o contrario.
O Demo Viewer nao deve importar `src.jutta_ped.service.*` diretamente em
nenhum outro lugar; tudo passa por aqui. Isso deixa a fronteira auditavel
(um `grep` neste arquivo mostra a superficie inteira que a UI usa do
servico) e facilita trocar o modo de acesso no futuro sem tocar a UI.

Hoje (modo de compatibilidade) o Demo Viewer ainda instancia e roda
`PediatriaService` **em processo**, direto na thread do Qt -- e por isso
esse cliente reexporta a classe do servico, nao so um wrapper HTTP. Isso e
uma dívida arquitetural conhecida e documentada (ver `docs/pediatria_service_api.md`):
o Core do WebGuardiao, quando for ativar este plugin via checkbox, deve
falar com o servico por `stream_ref`/API, nao instanciar `PediatriaService`
localmente como a UI faz hoje. A migracao para "UI 100% cliente HTTP" fica
para uma rodada futura; por enquanto, reexportar aqui pelo menos garante que
so este arquivo precisa mudar quando isso acontecer.

O launcher (start/stop/health do microservico local) ja e HTTP-only desde a
Rodada 3 e continua reexportado aqui tambem.

Caminho futuro (quando a UI virar 100% cliente HTTP): falar com o contrato
de runtime de plugin (`POST /instances`, `GET /instances/{id}`,
`GET /instances/{id}/overlay/latest`, `GET /instances/{id}/events`) descrito
em `plugin/README.md`, nao com `/sessions/*`/`/cameras/*` diretamente -- esse
e o mesmo contrato que o Core vai usar para ativar o plugin via checkbox, e
manter o Demo Viewer nele evita o cliente ter dois jeitos diferentes de
conversar com o mesmo servico.
"""

from __future__ import annotations

from src.jutta_ped.service import launcher as service_launcher
from src.jutta_ped.service.pediatria_service import (
    PediatriaFrameResult,
    PediatriaService,
    PediatriaServiceConfig,
    cpu_economical_overrides,
)
from src.jutta_ped.service.telemetry import (
    DiskUsageAccumulator,
    ResourceSampler,
    RunningStat,
    SessionTelemetry,
)

__all__ = [
    "service_launcher",
    "PediatriaFrameResult",
    "PediatriaService",
    "PediatriaServiceConfig",
    "cpu_economical_overrides",
    "DiskUsageAccumulator",
    "ResourceSampler",
    "RunningStat",
    "SessionTelemetry",
]
