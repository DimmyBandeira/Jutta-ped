# Bateria de referência — caminho atual da análise

Gerado por `tools/bateria_referencia.py relatorio`. Dados brutos em JSON na mesma pasta. Procedimento e critérios: [README](../README.md).

## 1. Ambiente e procedimento

* **Coleta:** 2026-10-04T14:20:45 · **commit:** `626d29c65e` (feature/motor-plugins-nos), 1 arquivo(s) rastreado(s) modificado(s) na coleta.
* **Máquina:** Windows-11-10.0.26200-SP0 · 11th Gen Intel(R) Core(TM) i9-11900H @ 2.50GHz · 16 threads lógicos (8 núcleos físicos) · 16048 MB de RAM.
* **GPUs:** NVIDIA GeForce RTX 3050 Laptop GPU (nvml, 4096 MB); 11th Gen Intel(R) Core(TM) i9-11900H @ 2.50GHz (openvino); Intel(R) UHD Graphics (iGPU) (openvino); NVIDIA GeForce RTX 3050 Laptop GPU (dGPU) (openvino)
* **Bibliotecas:** cv2 4.10.0, numpy 2.3.5, openvino 2026.2.1-21919-ede283a88e3-releases/2026/2, psutil 7.2.2, pynvml ?, torch 2.5.1+cu121, ultralytics 8.4.57
* **Modelos (hash de conteúdo):** especialista: `pediatria_child_detector_v6_jutta_openvino_model` `699472aa7d2885fa…`; pessoa: `yolo11n_openvino_model` `c6424a4ec6d6a899…`

**Fontes (reproduzidas em tempo real e em laço, como câmera ao vivo):**

| arquivo | resolução | fps | quadros | duração s | SHA-256 |
|---|---|---|---|---|---|
| DVR-1-Ch.7-8 5-- PAV-Hall-ELEV SERVI.dav | 1280×720 | 15.0 | 480 | 32.0 | `63bf6a2912e55e3d…` |
| DVR-3-Ch.0-1 ACESSO-BLINDEX.avi | 960×480 | 30.0 | 441 | 14.7 | `7f4d41f23f752913…` |
| Corredor pediatria.mp4 | 1280×720 | 24.0 | 240 | 10.0 | `d99d666598db7ff2…` |
| Corredor pediatria 2.mp4 | 1280×720 | 24.0 | 240 | 10.0 | `e0e6707ab19d36bd…` |

## 2. Ensaios de 1, 2 e 4 câmeras

Cada ensaio roda em processo limpo; as métricas são da **janela estável** (após carga inicial e aquecimento, que ficam fora). `*` = pior câmera do ensaio. CPU em % do total da máquina (threads lógicos). `analysis_fps=0` = sem limite (comportamento anterior à mudança de cadência); `5` = padrão atual para fontes ao vivo.

| Ensaio | fps total | fps/câmera | descart. | CPU proc. % do total (média / p95) | núcleos eq. | CPU sist. % | RAM MB (média / pico) | VRAM Δ MB | GPU % | carga inicial s | 1º quadro s | idade em uso ms (p50 / p95)* | idade na publicação ms (p50 / p95)* | latência proc. ms (p50 / p95)* |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 cam · fps 0 · cpu · r1 | 15.0 | 15.0 | 0 | 44.8 / 46.0 | 7.2 | 57.9 | 1144 / 1281 | 0 | 0.0 | 9.7 | 11.8 | 46 / 84 | 12 / 40 | 12 / 40 |
| 2 cam · fps 0 · cpu · r1 | 44.5 | 15.0–29.5 | 21 | 85.5 / 88.9 | 13.7 | 97.1 | 1547 / 1593 | 0 | 0.0 | 8.4 | 11.1 | 49 / 97 | 15 / 55 | 14 / 55 |
| 4 cam · fps 0 · cpu · r1 | 74.1 | 12.0–26.5 | 850 | 72.4 / 77.9 | 11.6 | 100.0 | 2375 / 2411 | 0 | 0.0 | 9.0 | 16.1 | 117 / 490 | 41 / 339 | 32 / 320 |
| 1 cam · fps 5 · cpu · r1 | 4.9 | 4.9 | 454 | 25.8 / 33.9 | 4.1 | 38.6 | 1145 / 1280 | 0 | 0.0 | 10.7 | 12.9 | 148 / 248 | 46 / 86 | 11 / 37 |
| 2 cam · fps 5 · cpu · r1 | 9.8 | 4.9 | 1582 | 50.6 / 58.2 | 8.1 | 62.1 | 1480 / 1539 | 0 | 0.0 | 9.4 | 11.3 | 153 / 252 | 50 / 91 | 12 / 57 |
| 4 cam · fps 5 · cpu · r1 | 19.6 | 4.9–4.9 | 3302 | 75.4 / 82.6 | 12.1 | 92.2 | 2191 / 2356 | 0 | 0.0 | 9.5 | 17.1 | 158 / 261 | 50 / 109 | 18 / 88 |
| 1 cam · fps 5 · cuda:0 · r1 | 4.9 | 4.9 | 454 | 2.3 / 2.8 | 0.4 | 14.2 | 1231 / 1234 | 186 | 23.1 | 4.1 | 5.8 | 188 / 286 | 88 / 137 | 46 / 92 |
| 4 cam · fps 5 · cuda:0 · r1 | 19.6 | 4.9 | 3299 | 5.2 / 6.3 | 0.8 | 19.2 | 1589 / 1597 | 392 | 26.2 | 1.1 | 4.8 | 167 / 266 | 61 / 113 | 27 / 93 |
| 1 cam · fps 0 · cpu · r2 | 15.0 | 15.0 | 0 | 44.8 / 46.0 | 7.2 | 63.2 | 1144 / 1292 | 0 | 0.0 | 16.0 | 19.0 | 47 / 88 | 13 / 40 | 12 / 40 |
| 2 cam · fps 0 · cpu · r2 | 44.4 | 15.0–29.4 | 26 | 68.1 / 78.6 | 10.9 | 99.9 | 1532 / 1627 | 0 | 0.0 | 10.7 | 14.3 | 52 / 104 | 16 / 60 | 16 / 59 |
| 1 cam · fps 5 · cpu · r2 | 4.9 | 4.9 | 454 | 26.0 / 32.2 | 4.2 | 38.2 | 1170 / 1291 | 0 | 0.0 | 11.3 | 13.5 | 150 / 252 | 46 / 87 | 11 / 38 |
| 2 cam · fps 5 · cpu · r2 | 9.8 | 4.9–4.9 | 1581 | 49.5 / 57.8 | 7.9 | 63.5 | 1477 / 1510 | 0 | 0.0 | 9.1 | 11.1 | 152 / 256 | 48 / 93 | 12 / 49 |
| 4 cam · fps 5 · cpu · r2 | 19.6 | 4.9 | 3296 | 54.7 / 64.0 | 8.8 | 77.7 | 2376 / 2396 | 0 | 0.0 | 8.6 | 16.5 | 161 / 273 | 54 / 135 | 26 / 107 |
| 1 cam · fps 5 · cuda:0 · r2 | 4.9 | 4.9 | 454 | 2.4 / 3.0 | 0.4 | 14.4 | 1229 / 1232 | 186 | 24.9 | 4.5 | 5.9 | 185 / 289 | 84 / 133 | 46 / 90 |
| 4 cam · fps 5 · cuda:0 · r2 | 19.6 | 4.9–4.9 | 3297 | 7.5 / 8.8 | 1.2 | 20.4 | 1602 / 1614 | 396 | 26.9 | 1.1 | 4.9 | 188 / 302 | 80 / 155 | 45 / 126 |
| 1 cam · fps 0 · cpu · r3 | 15.0 | 15.0 | 0 | 44.9 / 46.1 | 7.2 | 57.1 | 1170 / 1274 | 0 | 0.0 | 9.2 | 11.3 | 46 / 87 | 2 / 37 | 12 / 36 |
| 2 cam · fps 0 · cpu · r3 | 44.7 | 15.0–29.7 | 14 | 82.9 / 86.9 | 13.3 | 98.6 | 1508 / 1561 | 0 | 0.0 | 9.2 | 11.7 | 49 / 96 | 15 / 54 | 14 / 54 |
| 4 cam · fps 0 · cpu · r3 | 74.8 | 10.1–27.9 | 815 | 72.8 / 77.0 | 11.7 | 100.0 | 2334 / 2360 | 0 | 0.0 | 9.1 | 14.7 | 180 / 773 | 23 / 556 | 20 / 580 |
| 1 cam · fps 5 · cpu · r3 | 4.9 | 4.9 | 453 | 26.4 / 35.8 | 4.2 | 38.3 | 1147 / 1288 | 0 | 0.0 | 9.7 | 11.8 | 155 / 259 | 50 / 92 | 11 / 36 |
| 2 cam · fps 5 · cpu · r3 | 9.8 | 4.9–4.9 | 1582 | 49.0 / 62.3 | 7.8 | 63.5 | 1481 / 1519 | 0 | 0.0 | 8.9 | 10.9 | 158 / 259 | 51 / 104 | 12 / 57 |
| 4 cam · fps 5 · cpu · r3 | 19.6 | 4.9–4.9 | 3298 | 73.4 / 77.5 | 11.8 | 93.6 | 2327 / 2345 | 0 | 0.0 | 8.9 | 14.0 | 162 / 270 | 51 / 130 | 19 / 107 |
| 1 cam · fps 5 · cuda:0 · r3 | 4.9 | 4.9 | 453 | 2.4 / 3.0 | 0.4 | 14.1 | 1234 / 1236 | 186 | 22.9 | 4.3 | 5.2 | 189 / 296 | 88 / 140 | 46 / 87 |
| 4 cam · fps 5 · cuda:0 · r3 | 19.6 | 4.9–4.9 | 3301 | 6.0 / 8.1 | 1.0 | 21.0 | 1575 / 1588 | 416 | 23.1 | 1.2 | 5.0 | 168 / 272 | 63 / 136 | 28 / 90 |
| 4 cam · fps 0 · cpu · r2 | 82.1 | 14.8–27.6 | 485 | 69.9 / 77.3 | 11.2 | 100.0 | 2378 / 2401 | 0 | 0.0 | 9.6 | 18.8 | 56 / 942 | 26 / 102 | 23 / 98 |

### Variação entre repetições

Cada repetição é um processo novo. Mostra mín–máx entre as repetições (n = número de execuções).

| configuração | n | fps total | CPU proc. % (média) | CPU sist. % (média) | RAM MB (média) | idade em uso p95 ms* | latência proc. p95 ms* |
|---|---|---|---|---|---|---|---|
| c1_fps0_auto | 3 | 15.0–15.0 | 44.8–44.9 | 57.1–63.2 | 1144–1170 | 84–88 | 36–40 |
| c2_fps0_auto | 3 | 44.4–44.7 | 68.1–85.5 | 97.1–99.9 | 1508–1547 | 96–104 | 54–59 |
| c4_fps0_auto | 3 | 74.1–82.1 | 69.9–72.8 | 100.0 | 2334–2378 | 490–942 | 98–580 |
| c1_fps5_auto | 3 | 4.9–4.9 | 25.8–26.4 | 38.2–38.6 | 1145–1170 | 248–259 | 36–38 |
| c2_fps5_auto | 3 | 9.8–9.8 | 49.0–50.6 | 62.1–63.5 | 1477–1481 | 252–259 | 49–57 |
| c4_fps5_auto | 3 | 19.6–19.6 | 54.7–75.4 | 77.7–93.6 | 2191–2376 | 261–273 | 88–107 |
| c1_fps5_cuda | 3 | 4.9–4.9 | 2.3–2.4 | 14.1–14.4 | 1229–1234 | 286–296 | 87–92 |
| c4_fps5_cuda | 3 | 19.6–19.6 | 5.2–7.5 | 19.2–21.0 | 1575–1602 | 266–302 | 90–126 |

### Janela e fases por ensaio

| ensaio | aquecimento s | janela estável s | import. s | amostras de recursos | alertas / suprimidos | colocação |
|---|---|---|---|---|---|---|
| c1_fps0_auto_r1 | 15.0 | 45.0 | 0.05 | 45 | 2 / 2 | bat0=cpu/openvino |
| c2_fps0_auto_r1 | 15.0 | 45.0 | 0.04 | 44 | 9 / 10 | bat0=cpu/openvino |
| c4_fps0_auto_r1 | 15.0 | 45.0 | 0.06 | 44 | 14 / 26 | bat0=cpu/openvino |
| c1_fps5_auto_r1 | 15.0 | 45.0 | 0.06 | 44 | 2 / 5 | bat0=cpu/openvino |
| c2_fps5_auto_r1 | 15.0 | 45.0 | 0.04 | 45 | 5 / 11 | bat0=cpu/openvino |
| c4_fps5_auto_r1 | 15.0 | 45.0 | 0.05 | 45 | 12 / 25 | bat0=cpu/openvino |
| c1_fps5_cuda_r1 | 15.0 | 45.0 | 0.06 | 43 | 4 / 9 | bat0=cuda:0/pytorch |
| c4_fps5_cuda_r1 | 15.0 | 45.0 | 0.04 | 44 | 10 / 27 | bat0=cuda:0/pytorch |
| c1_fps0_auto_r2 | 15.0 | 45.0 | 0.09 | 45 | 5 / 5 | bat0=cpu/openvino |
| c2_fps0_auto_r2 | 15.0 | 45.0 | 0.06 | 44 | 7 / 8 | bat0=cpu/openvino |
| c1_fps5_auto_r2 | 15.0 | 45.0 | 0.05 | 44 | 2 / 5 | bat0=cpu/openvino |
| c2_fps5_auto_r2 | 15.0 | 45.0 | 0.05 | 44 | 8 / 12 | bat0=cpu/openvino |
| c4_fps5_auto_r2 | 15.0 | 45.0 | 0.05 | 45 | 8 / 22 | bat0=cpu/openvino |
| c1_fps5_cuda_r2 | 15.0 | 45.0 | 0.06 | 43 | 4 / 9 | bat0=cuda:0/pytorch |
| c4_fps5_cuda_r2 | 15.0 | 45.0 | 0.05 | 43 | 14 / 25 | bat0=cuda:0/pytorch |
| c1_fps0_auto_r3 | 15.0 | 45.0 | 0.04 | 45 | 0 / 0 | bat0=cpu/openvino |
| c2_fps0_auto_r3 | 15.0 | 45.0 | 0.05 | 44 | 8 / 9 | bat0=cpu/openvino |
| c4_fps0_auto_r3 | 15.0 | 45.0 | 0.05 | 44 | 18 / 22 | bat0=cpu/openvino |
| c1_fps5_auto_r3 | 15.0 | 45.0 | 0.05 | 44 | 2 / 4 | bat0=cpu/openvino |
| c2_fps5_auto_r3 | 15.0 | 45.0 | 0.05 | 44 | 6 / 10 | bat0=cpu/openvino |
| c4_fps5_auto_r3 | 15.0 | 45.0 | 0.05 | 44 | 11 / 25 | bat0=cpu/openvino |
| c1_fps5_cuda_r3 | 15.0 | 45.0 | 0.05 | 44 | 4 / 9 | bat0=cuda:0/pytorch |
| c4_fps5_cuda_r3 | 15.0 | 45.0 | 0.04 | 43 | 12 / 30 | bat0=cuda:0/pytorch |
| c4_fps0_auto_r2 | 15.0 | 45.0 | 0.05 | 45 | 21 / 29 | bat0=cpu/openvino |

## 3. Hipótese do GIL (separada da medição de capacidade)

### 3.1 Perfil de uma câmera (`DVR-1-Ch.7-8 5-- PAV-Hall-ELEV SERVI.dav`, 150 quadros; mesmos quadros e estado reiniciado nas duas passadas; cpu/openvino)

* Sem perfilador: **95.268 fps** (10.5 ms/quadro); com cProfile: 85.733 fps (overhead 11.1 %).
* Do tempo próprio total: **Python puro (executa bytecode, segura o GIL): 18.02 %** · envoltório Python de inferência nativa (OpenVINO): 56.62 % · builtins/C visíveis (OpenCV, NumPy, imencode…): 25.36 %.
* Python puro por pacote (% do total): modulo/pediatria 1.48, numpy 1.37, openvino 1.63, src/jutta_ped 0.33, stdlib/outros 0.58, torch 1.6

Maiores consumidores de Python puro: `nms.py:13(non_max_suppression)` 1.62 %; `data_dispatcher.py:74(_)` 1.32 %; `predictor.py:279(stream_inference)` 1.22 %; `predictor.py:154(preprocess)` 1.21 %; `module.py:1740(_call_impl)` 0.95 %

Envoltórios de inferência: `_ov_api.py:119(infer)` 56.59 %; `_ov_api.py:376(__call__)` 0.03 %

Maiores builtins/C: `<method 'copy' of 'numpy.ndarray' objects>` 6.0 %; `<imencode>` 4.41 %; `<method 'astype' of 'numpy.ndarray' objects>` 2.6 %; `<resize>` 2.39 %; `<built-in method numpy.ascontiguousarray>` 1.81 %

_pct_python_puro = tempo proprio em funcoes Python (executam bytecode, seguram o GIL); pct_envoltorio_de_inferencia_nativa = funcoes Python que so esperam uma chamada nativa de inferencia (OpenVINO); pct_nativo_builtin = builtins/C visiveis (OpenCV, NumPy, imencode). O cProfile infla a fatia Python (ver overhead). Esperar o GIL so e medido com varias threads: ver `escala`._

### 3.2 Threads × processos — 4 câmeras, analysis_fps=0, dispositivo `auto`

| cenário | fps total | CPU sistema % (média / p95) | descartados | RAM |
|---|---|---|---|---|
| A) threads em 1 processo | 75.6 | 100.0 / 100.0 | 782 | 2364 MB |
| B) 4 processos × 1 câmera | 89.1 | 100.0 / 100.0 | 177 | 4114 MB (soma) |

* **Razão B/A:** 1.178 · **CPU média do sistema em A:** 100.0 %.
* **Conclusão pela regra pré-registrada:** **limitado por CPU (nao pelo GIL)**. Regra: razao>=1.30 e CPU<80% => GIL; CPU>=80% => limitado por CPU; razao<1.10 => sem diferenca; senao inconclusivo.

### 3.2 Threads × processos — 4 câmeras, analysis_fps=0, dispositivo `cuda`

| cenário | fps total | CPU sistema % (média / p95) | descartados | RAM |
|---|---|---|---|---|
| A) threads em 1 processo | 55.2 | 39.8 / 51.2 | 1700 | 1588 MB |
| B) 4 processos × 1 câmera | 81.7 | 53.1 / 64.1 | 507 | 3973 MB (soma) |

* **Razão B/A:** 1.48 · **CPU média do sistema em A:** 39.8 %.
* **Conclusão pela regra pré-registrada:** **consistente com o GIL como gargalo**. Regra: razao>=1.30 e CPU<80% => GIL; CPU>=80% => limitado por CPU; razao<1.10 => sem diferenca; senao inconclusivo.

## 4. Qualidade dos alertas (episódios)

| vídeo | alertas | suprimidos | trilhas nos alertas | revisado | detectados | falsos | repetidos | trocas de trilha | observação |
|---|---|---|---|---|---|---|---|---|---|
| DVR-1-Ch.7-8 5-- PAV-Hall-ELEV SERVI.dav | 3 | 3 | [2, 4, 20] | **não** | — | — | — | — | nenhum arquivo de episodios para este video |
| DVR-3-Ch.0-1 ACESSO-BLINDEX.avi | 2 | 2 | [1, 18] | **não** | — | — | — | — | nenhum arquivo de episodios para este video |

Métricas de qualidade só existem para vídeos com episódios **revisados por uma pessoa**; `—` = indisponível (não estimado).

## 5. Leitura dos resultados

Interpretação escrita pelo autor da rodada depois de ver os dados. Tudo abaixo cita números das seções anteriores; o que é **hipótese** está marcado
como tal. Nenhum número de qualidade de alerta existe sem revisão humana (seção 4).

### 5.1 Caminho atual na CPU (OpenVINO), 1, 2 e 4 câmeras

* **1 câmera sem limite de cadência** acompanha a fonte (15 fps) com 0 quadros descartados, ~7 núcleos equivalentes (45 % da máquina), 1,14 GB de RAM e
  idade da observação em uso p95 ≈ 85 ms. **2 câmeras** (15 e 30 fps de origem): 44–45 fps no total, CPU do sistema ≈ 98 %, p95 ≈ 100 ms.
* **4 câmeras sem limite saturam a CPU** (100 % do sistema nas três repetições): 74–82 fps no total (a soma nominal das fontes é 93 fps),
  485–850 quadros descartados e **idade em uso p95 de 490 a 942 ms** e latência de processamento p95 de 98 a 580 ms. A variação entre repetições é grande
  justamente na saturação; use o intervalo, não um valor.
* **A 5 fps por câmera (padrão atual das fontes ao vivo)** todas as configurações chegam a 4,9 fps/câmera, sem fila, com idade em uso p50 ≈ 150–160 ms e
  p95 ≈ 250–275 ms **independente do número de câmeras** (o limite é o período de 200 ms da cadência). Mas a CPU do sistema vai a 38 % (1 câmera), 62–64 % (2)
  e **78–94 % (4)**: quatro câmeras a 5 fps já consomem quase toda a folga da CPU desta máquina.
* **O uso de CPU não é proporcional ao trabalho (observação; causa não testada).** 1 câmera gasta 7,2 núcleos equivalentes para 15 fps (0,48 por fps)
  e 4,1 para 4,9 fps (0,84 por fps); 4 câmeras a 5 fps (19,6 fps) gastam 9–12 núcleos. *Hipótese:* as threads internas do OpenVINO esperam ativamente
  entre chamadas. Consequência prática: **não extrapolar capacidade a partir da CPU medida em cadência baixa**; medir o fps na saturação.
  O perfil (3.1) mostra que a câmera sozinha processa ~95 fps (10,5 ms/quadro) nestes quadros, mas 4 câmeras juntas ficam em 74–89 fps: o
  *throughput* total satura perto de 75–90 fps, qualquer que seja o número de câmeras.
* **Memória:** ~400 MB a mais por câmera (1,14 → 1,5 → 2,2–2,4 GB), porque cada sessão carrega os próprios modelos (a mesma ordem de grandeza do teste operacional de 03/10).
* **Partida:** carga inicial de 9–16 s por câmera e 11–19 s até o primeiro quadro (compilação dos modelos OpenVINO); relevante para reinícios a quente.

### 5.2 Caminho CUDA (PyTorch `.pt`), 1 e 4 câmeras a 5 fps

* CPU do processo 2,3–7,5 % do total (≈ 0,4–1,2 núcleo equivalente) contra 26 % e 55–75 % no caminho OpenVINO; CPU do sistema 14–21 %.
  GPU 23–27 % de utilização; **VRAM +186 MB (1 câmera) e +392–416 MB (4 câmeras)**; RAM 1,2–1,6 GB.
* Em troca, a latência por quadro é maior (p50 27–46 ms, p95 87–126 ms, contra p50 11–18 ms e p95 36–107 ms na CPU a 5 fps) e a idade em uso p95 fica em 266–302 ms (CPU: 252–273 ms).
* **Não** foi avaliada a qualidade: os alertas contados diferem entre os dois caminhos nos mesmos vídeos (1 câmera a 5 fps: 2 alertas na CPU e 4 na GPU, nas três repetições de cada), e isso não permite dizer qual está certo.

### 5.3 Hipótese do GIL (separada da capacidade)

* **Perfil, 1 câmera, OpenVINO/CPU, mesmos quadros nas duas passadas:** 18 % do tempo em **Python puro** (executa bytecode, segura o GIL), 57 % em
  `openvino/_ov_api.py::infer` (espera de inferência nativa) e 25 % em builtins/C visíveis (NumPy `copy`/`astype`, OpenCV `resize`, `imencode`). Overhead do cProfile: 11 %.
  Ou seja, **nesse caminho a maior parte do tempo está fora do bytecode Python**.
* **Threads × processos, 4 câmeras sem limite**, com a regra pré-registrada:
  * **OpenVINO/CPU:** razão processos/threads = 1,18 e CPU do sistema = 100 % nos dois cenários ⇒ **limitado por CPU, não pelo GIL**.
  * **CUDA:** razão = **1,48** (55,2 → 81,7 fps) com CPU do sistema em 40 % nas threads ⇒ **consistente com o GIL como gargalo** nesse caminho.
    Esse resultado é coerente com a medição exploratória anterior (8 threads em CUDA, ~41 fps no total com 21 % de CPU), que motivou a hipótese.
* **O que isto não prova:** "consistente com" não é medir espera pelo GIL (não há `py-spy --gil` aqui). Alternativas não excluídas: serialização no
  mesmo contexto/stream CUDA dentro de um processo, bloqueios do PyTorch/Ultralytics, ou o fato de que 4 processos usam 4 contextos CUDA (a GPU também é compartilhada).
  Uma única execução por cenário; sem repetição.
* **Implicação de desenho (a validar na etapa 04):** no caminho GPU, mais threads de sessão no mesmo processo escalam pior do que processos separados;
  um executor compartilhado com lotes (um modelo para várias câmeras) ataca justamente isso, mas é preciso medir antes de assumir.

### 5.4 Idade da observação e a meta de ~1 s

Em todas as configurações **sem saturação** a idade em uso p95 ficou abaixo de 0,31 s (≤ 0,26 s na CPU a 5 fps; ≤ 0,30 s em CUDA) e a idade na publicação p95 abaixo de
0,16 s. Só as 4 câmeras sem limite (CPU saturada) ultrapassam: idade em uso p95 de 0,49 a 0,94 s e latência de processamento p95 de 0,10 a 0,58 s. **A meta de ~1 s de idade de observação
é atendida folgadamente fora da saturação**, mas isto mede só o *resultado da IA*: o atraso até o alerta inclui ainda a persistência/confirmação das regras (segundos), a captura
física, a rede e o navegador, que a bateria não cobre.

### 5.5 Alertas, e o que ainda falta para dizer "perdido / falso / repetido"

* Vídeos de 19/06 (arquivo inteiro, todos os quadros, modelo v6 atual): **DVR-1** emitiu 3 alertas `CHILD_SEPARATED` (t = 15,7 s trilha 4; 21,3 s trilha 20; 31,1 s trilha 2);
  **DVR-3** emitiu 2 alertas `CHILD_ALONE` (t = 6,3 s trilha 1; 6,6 s trilha 18). No relatório de 19/06 (modelo v5) os dois vídeos davam 0 alertas.
* **Observação visual, não é rótulo:** os quadros de 6,3 s e 6,6 s do DVR-3 mostram o que parece ser a mesma criança, no mesmo lugar, com trilhas diferentes (1 e 18),
  a 0,3 s de distância: **candidato a repetição por troca de trilha**. Quem decide é a revisão humana (`docs/referencia/README.md`, seção 6).
* **Reprodutibilidade dos alertas em streaming é baixa sem limite de cadência:** 1 câmera sem limite emitiu 2, 5 e 0 alertas nas três repetições e 4 câmeras sem limite, 14, 21 e 18 (com 5 fps, 1 câmera foi estável: 2, 2, 2 na CPU e 4, 4, 4 na GPU). Causa **não investigada**
  (candidatas: dependência de tempo real do cooldown/latch, descarte de quadros, reinício das trilhas a cada volta do laço). Consequência: **a comparação com a migração
  deve usar os episódios revisados em análise de arquivo (determinística), não as contagens dos ensaios de streaming**, que são só descritivas.
* Métricas de qualidade: **indisponíveis** até haver episódios revisados. Nenhuma foi estimada.

### 5.6 Defeitos da própria bateria encontrados e corrigidos

1. A primeira versão do perfil comparava passadas com quadros e estado diferentes (overhead de −71 %) e contava o *wrapper* Python do `infer` como bytecode (inflava o "Python" para 68 %).
   Corrigido: mesmos quadros, estado reiniciado, passada de aquecimento e classe separada para o envoltório de inferência nativa.
2. Em `c4_fps0_auto_r2` uma câmera leu 0 quadros na janela: o reposicionamento do arquivo `.dav` no fim do laço falhou de forma intermitente e a sessão terminou.
   O ensaio foi **preservado como evidência** (`ensaios/c4_fps0_auto_r2_INVALIDO_camera_parou.json`), a fonte passou a **reabrir** o arquivo e o ensaio foi refeito. Os demais 24 ensaios foram
   auditados: todos tinham as 4/2/1 câmeras analisando na janela. Os ensaios r1–r3 anteriores a essa correção usaram a versão antiga da fonte; apenas o r2 de 4 câmeras sem limite foi refeito com a nova.
3. O módulo de GPU (`nvidia-ml-py`) não estava no ambiente virtual; sem ele as colunas de GPU/VRAM saíam vazias. Instalado e declarado em `requirements-dev.txt`.

### 5.7 Próximos passos que os dados indicam

1. **Revisão humana** dos episódios (pacote em `episodios/revisao/`) para obter perdidos/falsos/repetidos, incluindo vídeos sem criança sozinha como negativos.
2. Investigar a **não proporcionalidade da CPU** do OpenVINO (limitar threads/streams e repetir 1 e 4 câmeras a 5 fps) antes de dimensionar com base em CPU.
3. Medir a **espera pelo GIL** diretamente no caminho CUDA (ferramenta de amostragem de GIL) antes de decidir entre executor compartilhado e processos por câmera.
4. Investigar a **variação das contagens de alertas** entre repetições idênticas.
5. Repetir 4 câmeras sem limite com mais repetições (a saturação tem a maior dispersão).


## 6. Limitações

* Fontes são **arquivos de vídeo reproduzidos em tempo real**, não câmeras RTSP: não medem rede, decodificação H.264/H.265 em tempo real do go2rtc nem a latência do navegador.
* Poucos vídeos curtos (15–32 s) em laço: o conteúdo se repete e é limitado; resultados dependem da cena (número de pessoas) e não representam carga máxima de um hospital.
* Uma repetição por configuração (salvo indicação): sem estimativa de variância entre execuções; a dispersão mostrada é entre amostras dentro de uma janela.
* Máquina única (notebook com GPU integrada e NVIDIA de 4 GB); resultados não se transferem a outro hardware sem repetir.
* A instrumentação (`_process_frame` envolvido por cronômetros) e o amostrador (20 Hz) têm custo próprio, pequeno e não medido isoladamente.
* A idade "em uso" assume um consumidor que lê o resultado mais recente em instantes aleatórios; ela não inclui a captura física da câmera nem a rede.
* Perfil do GIL: cProfile infla o tempo Python; "tempo em bytecode" não é "tempo esperando o GIL". A comparação threads × processos tem confundidores declarados em `escala.py`.
* Qualidade (perdidos/falsos/repetidos) depende de episódios revisados por pessoa; sem revisão, só há contagem descritiva.
