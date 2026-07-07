# Pediatria MVP - rodada com videos reais 2026-06-19

Data: 2026-06-19
Jira: WEB-11
Branch: feature/modulo-pediatria
Escopo: diagnostico standalone/shadow do MVP pediatrico com videos reais locais.

## Ambiente

- Venv informada `C:\WebGuardião\webguardiao` nao iniciou: o executavel aponta para `C:\Users\drb-d\AppData\Local\Programs\Python\Python312\python.exe`, ausente na maquina.
- Rodada executada com fallback local `C:\Users\drb-d\anaconda3\envs\alerta-rededor\python.exe`.
- `YOLO_CONFIG_DIR` foi redirecionado para `C:\WebGuardião\pediatria_results\ultralytics_config` para evitar acesso negado em `AppData\Roaming\Ultralytics`.
- Modelo: `src\models\pediatria_child_detector_v5.pt`.
- Dispositivo: `cuda:0` / NVIDIA GeForce RTX 3050 Laptop GPU.

## Fontes avaliadas

- `storage\videos\DVR-1-Ch.7-8 5-- PAV-Hall-ELEV SERVI.dav`
- `storage\videos\DVR-3-Ch.0-1 ACESSO-BLINDEX.avi`

## Saidas geradas

- `pediatria_results\real_videos_20260619\dvr1_pav_hall_elev_servi_report.json`
- `pediatria_results\real_videos_20260619\dvr1_pav_hall_elev_servi_annotated.mp4`
- `pediatria_results\real_videos_20260619\dvr3_acesso_blindex_report.json`
- `pediatria_results\real_videos_20260619\dvr3_acesso_blindex_annotated.mp4`

## Resultado - DVR-1 PAV Hall Elev Servi

- Frames processados: 480.
- Deteccoes por papel: `adult=421`, `child=26`, `uncertain=3`.
- Estados estaveis: `UNCERTAIN=208`, `NO_CHILD=272`.
- Nenhum `CHILD_ALONE` ou `CHILD_SEPARATED`.

Achados:

- A crianca aparece como `child` em poucos frames e com confianca media baixa.
- Apenas 5 frames tiveram `child` com confianca `>= 0.60`.
- O `track_id=13`, que corresponde ao alvo infantil no trecho posterior, aparece inicialmente como `child` em 3 frames e depois como `adult` em 292 frames.
- Como `CompanionshipConfig.min_role_confidence=0.60` e `relationship_confirmation_frames=15`, o vinculo crianca-adulto nao se confirma antes da oscilacao para adulto.

Simulacao sobre o JSON da rodada:

- Reduzir somente `min_role_confidence` nao gerou separacao.
- `min_role_confidence=0.30` com `relationship_confirmation_frames=3` gerou `CHILD_SEPARATED` a partir do frame 181.
- Essa combinacao precisa de benchmark antes de virar default, pois reduz guardrails contra falso positivo.

## Resultado - DVR-3 Acesso Blindex

- Frames processados: 441.
- Deteccoes por papel: `adult=364`, `child=0`.
- Estados estaveis: `UNCERTAIN=229`, `NO_CHILD=212`.
- Nenhum `CHILD_ALONE` ou `CHILD_SEPARATED`.

Achado principal:

- O modelo v5 nao reconheceu nenhuma crianca como `child` neste video. Todas as pessoas detectadas foram classificadas como `adult`.
- Ajustes de `min_role_confidence` ou `relationship_confirmation_frames` nao resolvem este video, porque nao existe entrada `child` para o analisador de companhia.

## Diagnostico

O problema principal esta antes da regra de companhia:

1. O detector/classificador `adult/child` esta instavel no `.dav`.
2. O detector/classificador esta enviesado para `adult` no `.avi`.
3. A regra temporal atual e conservadora e depende de `child` persistente para criar vinculo.

## Ajuste contextual aplicado no MVP

Arquivos principais:

- `modulo/pediatria/detector_mvp.py`
- `modulo/pediatria/companionship.py`
- `tools/run_pediatria_popup_mvp.py`

Mudancas:

- Adicionado `PediatricRoleContextAdjuster`, usado antes da analise de companhia.
- O ajuste preserva memoria `child` por `track_id` quando ha evidencia infantil repetida.
- O ajuste promove candidato classificado como `adult` para `child` quando ele e pequeno em relacao a um adulto confiavel no mesmo plano visual.
- A companhia agora trata adulto distante como ausencia de adulto proximo; apos persistencia, o estado vira `CHILD_ALONE` em vez de permanecer `UNCERTAIN`.
- Escopo permanece standalone/shadow; nao publica alerta, nao altera dispatcher, ChromeBridge, WebHT, WebAcesso ou pipeline oficial.

## Resultado apos ajuste contextual

Saidas:

- `pediatria_results\real_videos_20260619_context\dvr1_pav_hall_elev_servi_report.json`
- `pediatria_results\real_videos_20260619_context\dvr1_pav_hall_elev_servi_annotated.mp4`
- `pediatria_results\real_videos_20260619_context\dvr3_acesso_blindex_report.json`
- `pediatria_results\real_videos_20260619_context\dvr3_acesso_blindex_annotated.mp4`

`DVR-1 PAV Hall Elev Servi`:

- Antes: `adult=421`, `child=26`, estados `UNCERTAIN=208`, `NO_CHILD=272`.
- Depois: `adult=123`, `child=324`, `uncertain=3`, estados `UNCERTAIN=130`, `NO_CHILD=72`, `CHILD_ALONE=278`.
- A crianca de vermelho passa a permanecer como `child` depois que o adulto sai do quadro.

`DVR-3 Acesso Blindex`:

- Antes: `adult=364`, `child=0`, estados `UNCERTAIN=229`, `NO_CHILD=212`.
- Depois: `adult=162`, `child=202`, estados `UNCERTAIN=238`, `CHILD_ALONE=179`, `NO_CHILD=24`.
- A crianca menor a direita passa a ser classificada como `child` pelo contexto de escala.

## Segunda crianca correndo e cooldown

Revisao do trecho tardio do `.avi`:

- A segunda crianca aparece entre os frames 339 e 408.
- O movimento fragmenta a passagem nos `track_id=1`, `2`, `15` e `21`.
- Antes do ajuste adicional, `track_id=15` tinha 19/19 deteccoes `adult` e `track_id=21` tinha 32/32 deteccoes `adult`.
- A causa era contextual: o ajuste preservava papel apenas no mesmo `track_id` e exigia um adulto simultaneo para comparar escala.

Ajuste aplicado:

- Memoria curta de escala infantil por cena, com profundidade aproximada pelo pe da bbox.
- A memoria de cena so nasce de `child` repetido ou de comparacao forte com adulto no mesmo quadro.
- Promocao por memoria nao se retroalimenta como nova referencia confiavel.
- Memoria por `track_id` foi limitada a 15 frames para evitar contaminacao quando o ByteTrack reutiliza IDs.

Resultado final do `.avi`:

- `track_id=15`: 19/19 deteccoes como `child`.
- `track_id=21`: 32/32 deteccoes como `child`.
- Janela dos frames 339-408: 55 quadros com deteccao `child`.
- Estado estavel do segundo episodio: `CHILD_ALONE` no frame 401.
- Adultos distantes dos frames 429-441 permaneceram `adult` apos limitar a memoria por ID.

Cooldown:

- O primeiro episodio fica `CHILD_ALONE` no frame 134 e o segundo no frame 401.
- O latch rearma o episodio apos 45 frames resolvidos, mas o segundo popup continua suprimido pelo cooldown global de 120 segundos por camera.
- Corrigida a inicializacao do latch para o primeiro alerta da camera nunca ser bloqueado por comparar com tempo zero.
- O default de 120 segundos nao foi alterado.

Saidas finais:

- `pediatria_results\real_videos_20260619_scene_memory\dvr1_pav_hall_elev_servi_report.json`
- `pediatria_results\real_videos_20260619_scene_memory\dvr1_pav_hall_elev_servi_annotated.mp4`
- `pediatria_results\real_videos_20260619_scene_memory\dvr3_acesso_blindex_report.json`
- `pediatria_results\real_videos_20260619_scene_memory\dvr3_acesso_blindex_annotated.mp4`

Validacao:

- Suite focada de papel, companhia e latch: `23 passed`.
- `compileall` dos modulos e testes pediatricos alterados: aprovado.
- Regressao no `.dav`: resultado preservado em `adult=123`, `child=324`, `uncertain=3` e `CHILD_ALONE=278`.

## Proximos passos recomendados

1. Extrair crops dos falsos `adult` nesses dois videos e montar lote de revisao humana.
2. Retreinar/calibrar o modelo pediatrico com esses exemplos reais antes de promover mudanca operacional.
3. Testar um modo diagnostico, nao default, com confirmacao curta de vinculo e role memory mais tolerante para videos DVR.
4. Rodar benchmark comparando falso positivo em videos anteriores antes de alterar `CompanionshipConfig`.

Rollback:

- Nenhuma regra, config operacional, dispatcher, UI oficial, ChromeBridge ou pipeline real foi alterado nesta rodada.
