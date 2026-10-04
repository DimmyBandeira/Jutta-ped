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
