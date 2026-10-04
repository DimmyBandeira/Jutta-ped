# Bateria de referência — procedimento

Etapa 02 do [plano do motor](../plano_motor_plugins.md). Mede o **caminho atual** (sessões de IA com modelos por câmera, `PediatriaSessionManager`)
antes de migrar a pediatria, para que a migração seja comparada contra números e não contra impressão. Ferramenta:
`tools/bateria_referencia.py` (módulos em `tools/bateria/`). É **diagnóstico**: não roda na suíte de testes (a lógica pura tem testes em
`tests/test_bateria_referencia.py`).

**Os quadros de revisão (JPG) contêm pessoas de câmeras reais e NÃO são versionados** (`.gitignore`); regenere com `episodios`. Resultados de cada rodada ficam em `docs/referencia/<data>/` (`RELATORIO.md` + JSON brutos). O relatório só transcreve dados; a interpretação
fica em `leitura.md` da mesma pasta.

## 1. Como repetir

Pré-requisitos: `pip install -r requirements-mvp.txt -r requirements-dev.txt` (inclui `nvidia-ml-py` para GPU NVIDIA), os modelos de
`src/models/` e os vídeos de `videos/`. **Feche outros programas pesados**: a bateria usa a máquina inteira e qualquer carga paralela contamina
as medições. Anote a energia (tomada/bateria) e o plano de energia do Windows.

```
python tools/bateria_referencia.py ambiente   --saida docs/referencia/AAAA-MM-DD
python tools/bateria_referencia.py matriz     --saida docs/referencia/AAAA-MM-DD      # ~15 min
python tools/bateria_referencia.py perfil     --saida docs/referencia/AAAA-MM-DD
python tools/bateria_referencia.py escala     --saida docs/referencia/AAAA-MM-DD      # ~8 min
python tools/bateria_referencia.py episodios  --saida docs/referencia/AAAA-MM-DD
python tools/bateria_referencia.py relatorio  --saida docs/referencia/AAAA-MM-DD
```

Opções úteis: `matriz --repeticoes 3` (variância entre execuções), `--cameras 1 2 4`, `--analysis-fps 0 5`, `--extra-cuda 1 4`,
`--aquecimento 15 --estavel 45`, `--videos <arquivos>` (padrão: os dois DVR de 19/06 + os dois corredores).

## 2. O que cada passo registra

| Passo | Registra |
|---|---|
| `ambiente` | SO, CPU, RAM, GPUs (NVML e OpenVINO), versões de bibliotecas, commit e arquivos modificados, **hash dos modelos** (arquivo: SHA-256; pasta OpenVINO: `jutta-dir-v1`), **hash e propriedades dos vídeos**, configuração padrão da sessão |
| `matriz` | Um ensaio por configuração, **cada um em processo novo**: 1, 2 e 4 câmeras × `analysis_fps` 0 (sem limite) e 5 (padrão atual) no dispositivo `auto` (OpenVINO em CPU), mais 1 e 4 câmeras com `cuda` (PyTorch/GPU) a 5 fps |
| `perfil` | Uma câmera, sem e com cProfile: fatia do tempo em funções Python × chamadas nativas, por pacote |
| `escala` | 4 câmeras como **threads** × 4 **processos** de 1 câmera, janelas alinhadas; veredito por regra pré-registrada |
| `episodios` | Linha do tempo de alertas dos dois vídeos DVR, pacote de revisão e, se houver revisão, métricas |

## 2.1 Validade de um ensaio

Cada ensaio grava `validade` e o `estado_final_das_sessoes`. É **inválido** se alguma câmera analisou 0 quadros na janela estável ou a sessão não estava em
execução no fim dela. Ensaio inválido aparece marcado (`⚠ INVÁLIDO`) no relatório e fica fora da variação entre repetições; **não apague**: preserve-o
(`..._INVALIDO_<motivo>.json`) e refaça o ensaio. A matriz acumula repetições em `ensaios/indice.json` (`--repeticao-inicial N`).

## 3. Fases de um ensaio (nunca misturadas)

1. **Importação** do runtime e **carga inicial** por câmera (carregar os dois modelos e abrir a fonte) e **primeiro quadro analisado**.
2. **Aquecimento** (15 s): descartado.
3. **Janela estável** (45 s): todas as métricas de operação. As amostras de recursos (1 Hz) e de idade (20 Hz) usadas são só as da janela.

Cada "câmera" é um arquivo de vídeo reproduzido **no ritmo nativo e em laço**, embrulhado pelo mesmo `LatestFrameCapture` das fontes de rede do runtime
(quadros acumulados são descartados quando a IA é mais lenta).

## 4. Métricas

* **fps efetivo** por câmera: quadros analisados na janela ÷ duração real da janela; **descartados** = quadros que a fonte entregou e a IA não leu.
* **CPU**: do processo em % do total da máquina (threads lógicos) e em núcleos equivalentes; CPU do sistema. **RAM**: RSS do processo (média da janela e pico do ensaio).
* **GPU**: utilização e memória usada (NVML, GPU NVIDIA); **VRAM Δ** = média da janela − uso antes de carregar modelos.
* **Idade da observação** (três medidas, todas em ms, a partir do instante em que a fonte entregou o quadro):
  * *na publicação*: quadro entregue → resultado disponível;
  * *em uso*: `agora − entrega do quadro do último resultado`, amostrada a cada 50 ms (o que um consumidor que lê o resultado mais recente enxerga; inclui a espera entre resultados);
  * *latência de processamento*: só o tempo dentro de `_process_frame`.

## 5. Hipótese do GIL (separada da capacidade)

O perfil mostra **onde está o tempo** (Python × nativo); a comparação threads × processos testa **se o ganho aparece sem o GIL**. A regra de decisão é fixada
**antes** de olhar os dados (ver `tools/bateria/escala.py`): razão ≥ 1,30 com CPU do sistema < 80 % ⇒ consistente com o GIL; CPU ≥ 80 % ⇒ limitado por CPU;
razão < 1,10 ⇒ sem diferença; senão inconclusivo. Nenhum dos dois resultados, sozinho, "prova" o GIL; o relatório descreve os confundidores.

## 6. Qualidade dos alertas: episódios revisados

A bateria **não rotula nada**. O comando `episodios` analisa cada vídeo inteiro (todos os quadros) e entrega em `episodios/revisao/<vídeo>/`:
os quadros de cada alerta, `linha_do_tempo.jpg` (um quadro a cada 2 s, com o tempo) e `episodios.rascunho.json`.

Para virar medida, uma pessoa preenche o arquivo e o salva em `docs/referencia/episodios/<nome-do-vídeo-sem-extensão>.json`:

```json
{"video": "x.dav", "sha256": "<do vídeo>", "revisado": true, "revisor": "Nome", "data": "AAAA-MM-DD",
 "revisao_completa_do_video": true,
 "episodios": [{"id": "e1", "inicio_s": 3.0, "fim_s": 9.5, "estado_esperado": "CHILD_ALONE|CHILD_SEPARATED|qualquer", "observacao": "..."}]}
```

Regras do avaliador (`tools/bateria/episodios.py`):
* só calcula métricas se o SHA-256 confere, `revisado` é verdadeiro e `revisao_completa_do_video` é verdadeiro (sem a revisão completa não se pode chamar um alerta de "falso");
* **detectado**: há alerta (`popup_alert`) com estado esperado dentro de `[inicio − tol, fim + tol]` (tolerância padrão 2 s); **perdido**: nenhum;
* **falso**: alerta que não casa com nenhum episódio; **repetido**: alertas além do primeiro dentro do mesmo episódio; **troca de trilha**: trilhas distintas entre os alertas do mesmo episódio menos 1;
* **negativos**: o que está fora dos episódios, numa revisão completa. Inclua vídeos **sem nenhum episódio** (`"episodios": []`) para medir falsos alertas.

Candidatos a episódios positivos (relatório de 19/06, [pediatria_videos_reais_2026-06-19.md](../pediatria_videos_reais_2026-06-19.md)): `DVR-1-Ch.7-8 5-- PAV-Hall-ELEV SERVI.dav`
(criança que o modelo v5 quase não reconhece como `child`) e `DVR-3-Ch.0-1 ACESSO-BLINDEX.avi` (nenhuma criança reconhecida). Sem a revisão humana, os números
de qualidade ficam **indisponíveis**, não estimados.

## 7. Como ler e como comparar depois

* Compare **mesmos vídeos, mesmo hardware, mesmo procedimento**; confira o hash dos modelos e dos vídeos em `ambiente.json`.
* Uma repetição por configuração não dá variância: para decidir a migração use `--repeticoes 3` e compare pelo intervalo, não por um valor.
* Para o modo sombra da etapa 03, o critério de qualidade é o arquivo de episódios revisados, reaplicado ao motor novo com `avaliar`.
