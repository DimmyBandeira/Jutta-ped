# Etapa 02 — Referência reproduzível

Estado: **em andamento — custo e hipótese do GIL medidos; qualidade dos alertas BLOQUEADA por revisão humana dos episódios**.
Plano: [sistema-mãe, especialistas e nós](../plano_motor_plugins.md) · Branch: `feature/motor-plugins-nos` · Procedimento: [docs/referencia/README.md](../referencia/README.md).

## Objetivo

Medir o **caminho atual** da análise (antes de migrar a pediatria) com procedimento reproduzível: custo por câmera, hipótese do GIL separada da
capacidade e base para contar alertas perdidos, falsos e repetidos por troca de trilha em episódios revisados.

## Entregas

| Item | Onde |
|---|---|
| Ferramenta e módulos | `tools/bateria_referencia.py`, `tools/bateria/` (ambiente, fonte ritmada, amostrador, ensaio, matriz, perfil, escala, episódios, relatório) |
| Procedimento | [docs/referencia/README.md](../referencia/README.md) |
| Rodada de 04/10/2026 | [docs/referencia/2026-10-04/](../referencia/2026-10-04/RELATORIO.md): `RELATORIO.md`, `leitura.md`, `ambiente.json`, 25 ensaios + índice, `perfil.json`, `escala_auto.json`, `escala_cuda.json`, análises e avaliações de episódios |
| Testes da lógica | `tests/test_bateria_referencia.py` (22; inclui uma fumaça curta que pula sem os ativos locais) |

## Critérios do plano e situação

| Critério | Situação |
|---|---|
| Procedimento com versões, hashes de modelos, fontes e configuração | **Feito** (`ambiente.json`: commit, bibliotecas, hash `jutta-dir-v1` dos modelos, SHA-256 dos vídeos) |
| Ensaios de 1, 2 e 4 câmeras separando carga inicial, aquecimento e operação estável | **Feito** (3 repetições; 8 configurações: CPU sem limite e a 5 fps; CUDA a 5 fps com 1 e 4 câmeras) |
| CPU, RAM, VRAM, fps efetivo e idade das observações, por execução | **Feito** (um JSON por execução + tabela por execução) |
| Episódios revisados para perdidos/falsos/repetidos por troca de trilha | **PENDENTE — bloqueado.** Análise dos 2 vídeos de 19/06 e pacote de revisão prontos; **nenhuma métrica de qualidade foi gerada** porque não há revisão humana. Os números de qualidade ficam indisponíveis, não estimados |
| Perfil do GIL separado da hipótese | **Feito** (perfil de 1 câmera + threads × processos com regra pré-registrada, nos caminhos CPU e CUDA). Conclusão: ver `leitura.md` 5.3; não mede espera pelo GIL diretamente |

A etapa só pode ser arquivada quando houver episódios revisados (incluindo ao menos um vídeo sem criança sozinha como negativo) e a avaliação for gerada.

## Resultados principais (detalhes e ressalvas em `leitura.md`)

* CPU/OpenVINO: 1 e 2 câmeras acompanham as fontes sem fila; **4 câmeras sem limite saturam a CPU** (74–82 fps no total, idade em uso p95 0,49–0,94 s). A 5 fps, 4 câmeras
  chegam a 4,9 fps/câmera, com CPU do sistema em 78–94 %. O uso de CPU **não é proporcional ao trabalho** (hipótese não testada: espera ativa do OpenVINO).
* CUDA a 5 fps: CPU do processo 2–8 % do total, GPU 23–27 %, VRAM +186 MB (1 câmera) e +~400 MB (4 câmeras); latência por quadro maior.
* GIL: no caminho OpenVINO/CPU o gargalo é CPU (razão 1,18 com 100 % de CPU); no caminho CUDA a regra pré-registrada dá **consistente com o GIL** (razão 1,48 com 40 % de CPU). Evidência indireta.
* Alertas em streaming sem limite de cadência não são reprodutíveis (1 câmera: 2, 5 e 0 alertas em três repetições idênticas); causa não investigada.
* RAM: ~400 MB por câmera adicional.

## Defeitos da bateria achados e corrigidos na própria rodada

Perfil com passadas comparadas em quadros diferentes e wrapper do `infer` contado como Python; fonte `.dav` que parava ao fechar o laço (ensaio preservado como inválido e refeito);
ausência de `nvidia-ml-py` no ambiente virtual. Detalhes em `leitura.md` 5.6.

## Limitações

Fontes são vídeos reproduzidos em tempo real (não RTSP); poucos vídeos curtos em laço; máquina única; 3 repetições (a saturação ainda tem dispersão alta); instrumentação sem custo isolado;
idade da observação não inclui captura física, rede nem navegador; o perfil do GIL é indireto. Lista completa no `RELATORIO.md`.

## Pendências e próximo passo

1. **Revisão humana dos episódios** (quem: o responsável clínico/operacional; entrada: `docs/referencia/2026-10-04/episodios/revisao/` — regenerar com `episodios` pois os JPG não são versionados).
2. Investigar a CPU não proporcional do OpenVINO e a variação dos alertas entre repetições idênticas.
3. Medir espera pelo GIL no caminho CUDA antes de decidir entre executor compartilhado e processos por câmera (etapa 04).
4. Mais repetições da saturação (4 câmeras sem limite).

## Resultado de encerramento

Pendente (depende da revisão humana). Ao concluir: preencher data, episódios revisados e métricas, e mover este registro para `docs/historico/motor_plugins/`.
