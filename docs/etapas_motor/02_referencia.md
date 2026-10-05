# Etapa 02 — Referência reproduzível

Estado: **em andamento — custo e hipótese do GIL medidos; avaliador de qualidade v2 corrigido e pronto; métricas de qualidade BLOQUEADAS por confirmação humana**.
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
| Testes da lógica | `tests/test_bateria_referencia.py` (17 + 1 fumaça curta que pula sem os ativos locais) e `tests/test_bateria_episodios.py` (100, avaliador v2) |

## Critérios do plano e situação

| Critério | Situação |
|---|---|
| Procedimento com versões, hashes de modelos, fontes e configuração | **Feito** (`ambiente.json`: commit, bibliotecas, hash `jutta-dir-v1` dos modelos, SHA-256 dos vídeos) |
| Ensaios de 1, 2 e 4 câmeras separando carga inicial, aquecimento e operação estável | **Feito** (3 repetições; 8 configurações: CPU sem limite e a 5 fps; CUDA a 5 fps com 1 e 4 câmeras) |
| CPU, RAM, VRAM, fps efetivo e idade das observações, por execução | **Feito** (um JSON por execução + tabela por execução) |
| Episódios revisados para perdidos/falsos/repetidos por troca de trilha | **PENDENTE — bloqueado por confirmação humana.** Avaliador v2 corrigido (ver abaixo); análise com cobertura integral dos 2 vídeos de 19/06 e do candidato a negativo; propostas de IA preservadas como propostas. **Nenhuma métrica oficial de qualidade foi gerada** |
| Perfil do GIL separado da hipótese | **Feito** (perfil de 1 câmera + threads × processos com regra pré-registrada, nos caminhos CPU e CUDA). Conclusão: ver `leitura.md` 5.3; não mede espera pelo GIL diretamente |

A etapa só pode ser arquivada quando houver anotações **confirmadas por pessoa** (incluindo ao menos um negativo com procedência confirmada) e a avaliação oficial for gerada.

## Lote de correção do avaliador (04/10/2026, após a revisão visual por IA e a revisão independente)

Origem: [revisão visual por IA](../referencia/2026-10-04/revisao_codex.md) e [retorno](../retorno_claude_etapa02.md). Quatro achados reproduzidos e corrigidos em `tools/bateria/episodios.py` (avaliador **v2**), mais oito itens de um segundo revisor (subagente somente leitura):

| Achado | Correção |
|---|---|
| P1: um alerta detectava dois episódios (`usados` só afetava falsos) | Associação **exclusiva** por casamento máximo, preferência `(distância, inicio_s, id)`, independente da ordem; dentro do intervalo tem prioridade absoluta sobre a tolerância |
| P1: revisão sem lista virava negativo; metadados não validados | `validar_anotacao` (esquema 2): lista obrigatória, `[]` só com `negativo_confirmado`, tipos/intervalos/IDs, procedência com fonte/tipo/responsável, `origem_revisao`/`status` coerentes (IA nunca “confirmada”), revisor com cara de IA recusado |
| P2: “troca de trilha” (1→2→1 = 1) | `trilhas_adicionais` e `transicoes_de_trilha` (1→2→1 = 2), sem afirmar fragmentação |
| P1: análise incompleta/timeout não impedia avaliação | `aguardar_e_encerrar` lê o motivo **antes** do `stop()`; `cobertura` (EOF + quadros == arquivo); avaliador recalcula e recusa inconsistência |
| Sobreposição de sujeitos (DVR-3) | Alertas com 2+ candidatos listados como ambíguos; `detectados` é limite superior; `associacoes` manuais; aviso de sobreposição sem `sujeito_local` distintos |
| Revisor independente: fonte vazia aceita; filtro de IA estreito; `confirmar` herdava `metodo` contraditório e assumia procedência; leitura provisória com nomes de métrica oficial; entradas não-dict derrubavam o lote; `integral` confiado; critério de início ausente; tolerância creditando episódio vizinho; associação manual sem chave quebrava; empate de trilha por texto | Corrigidos, cada um com teste (58 → 100 testes do avaliador); `confirmar` exige uma flag por declaração e move o `metodo` para `derivada_de_proposta`; `reavaliar` recalcula sem inferência |
| Aceito como limite, não corrigido | O rascunho pode ser editado à mão para parecer humano/confirmado: tudo é declaração (6.1 do README); o avaliador não verifica identidade nem detecta arquivos reeditados coerentes |

**Dados desta rodada (avaliador v2, análise de arquivo inteiro, isolada):** cobertura integral comprovada nos 3 vídeos (480/480, 441/441, 395/395, EOF); os alertas dos dois DVR reproduzem exatamente os da análise v1 (mesmos instantes e trilhas): DVR-1 = 3 (15,667/21,267/31,133 s; trilhas 4/20/2), DVR-3 = 2 (6,333/6,567 s; trilhas 1/18); `stream-normal.mp4` (candidato a negativo, 13 s) = 0 alertas.
**Leitura provisória das propostas de IA (NÃO são métricas):** DVR-1: 1 episódio proposto, detectado, 2 repetidos, 2 trilhas adicionais; DVR-3: 2 propostos, 1 detectado, `dvr3-e2` sem alerta, 1 repetido; negativo: 0 alertas fora de episódios (sanidade de 13 s). Coincide com a leitura visual registrada pelo Codex. As análises v1 em `episodios/` ficam como histórico; nenhum número antigo foi alterado.

**Pendências para o bloqueio cair (visíveis nos JSON como `anotacao.pendencias`):** (1) pessoa identificada revê os vídeos inteiros e confirma os episódios; (2) fechar o `criterio_inicio` de cada episódio (DVR-1: 5 s, “adulto começa a se afastar”, ou 9,2 s, “adulto sai do campo”? muda o atraso em ~4 s); (3) confirmar a procedência do negativo (`stream-normal.mp4`) e dos DVR (real/sintético, quem confirmou); (4) decidir se a revisão é cega; (5) um negativo de 13 s é só sanidade: incluir negativo mais longo para estimar falsos por hora.

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

1. **Confirmação humana** das anotações (seção 6.5 do README): quem confirma é o responsável clínico/operacional; os JPG de apoio ficam em `episodios_v2/revisao/` (não versionados; regenerar com `episodios`).
2. Investigar a CPU não proporcional do OpenVINO e a variação dos alertas entre repetições idênticas.
3. Medir espera pelo GIL no caminho CUDA antes de decidir entre executor compartilhado e processos por câmera (etapa 04).
4. Mais repetições da saturação (4 câmeras sem limite).

## Resultado de encerramento

### Preparação de fechamento após `d16dab6`

Ver [resolução das cinco pendências](../referencia/2026-10-04/fechamento/README.md). Os 100 testes do avaliador foram reexecutados e passaram. Critérios desta referência definidos: DVR-1 em 9,2 s (saída do adulto do campo, não começo do afastamento); DVR-3 em 2,4 s e 10,4 s. Revisão explicitamente **não cega**. Rascunhos derivados preparados, sem alterar as propostas originais nem declarar confirmação humana.

O DVR-1 corresponde por SHA-256 a uma entrada do `5° ANDAR.zip`. Isso documenta a origem local, mas não substitui a declaração do responsável. Inventário do ZIP não encontrou vídeo longo: os 11 AVI somam 210 s, com possíveis equivalentes DAV que não devem ser contados duas vezes. Faltam nome/declarações do revisor, confirmação da procedência e gravação negativa mais longa. A etapa permanece em andamento.

Pendente (depende da revisão humana). Ao concluir: preencher data, episódios revisados e métricas, e mover este registro para `docs/historico/motor_plugins/`.
