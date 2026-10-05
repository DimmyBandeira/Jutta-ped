# Retomada do Claude — 05f e SimulaterDSA

## Ponto de partida

Continue na branch `feature/motor-plugins-nos`, a partir do commit de checkpoint
que contém este arquivo. O usuário autorizou consolidar o trabalho acumulado
do motor em sombra, dashboard e 05f. O commit não significa conclusão das etapas.
Os relatos antigos de “sem commit” nos registros descrevem a execução daqueles lotes.

Leia primeiro este arquivo, `docs/etapas_motor/05f_configuracao_camera.md` e
`docs/importacao_simulater_dsa.md`. Consulte os demais documentos por necessidade;
não refaça o histórico nem os quatro roteiros longos de validação.

## O que o Codex conferiu após a interrupção

- As alterações de JS e templates estavam gravadas: antes da primeira cena não
  há pedido de JPEG; miniaturas têm URL absoluta e quadro identificado por `q`.
- Suíte completa: **1.310 passaram, 9 pulados**, 1 aviso de depreciação
  Starlette/httpx, 215,28 s. A coleta antecedeu o novo teste de navegador abaixo.
  Não repetir a alegação de que são os mesmos 6 pulados antigos.
- Novo `tests/test_painel_diagnostico_navegador.py`: Edge headless em 390 × 844,
  espera sem JPEG, primeira cena e miniaturas sem erro HTTP/JS ou overflow.
  Passou separadamente em modo sintético e com `JUTTA_DIAG_RTSP` apontando para
  `rtsp://127.0.0.1:8554/001_bebe_colo_bebe_no_be_01`.
- Na modalidade real: quadro 1280 × 720, mestre OpenVINO CPU, 3 pessoas detectadas.
  HTTP, detector e navegador reais; gerente/go2rtc simulados na fixture e Cena
  injetada no diagnóstico. Não é teste do runtime completo, especialista ou player go2rtc.
- Não foram gravadas imagens de pessoas nem repetida a bateria de capacidade.

## Outra entrega do Codex: entrada SimulaterDSA

Já implementada, **não reimplementar**:

- **Câmeras → SimulaterDSA — importar cenário**, somente admin, CSRF e auditoria.
- Host do simulador + upload YAML `version: 1`; lê `server.rtsp_port` e
  `sources[].cameras` das fontes habilitadas. Não abre os caminhos `path` dos vídeos.
- Cria câmeras RTSP genéricas no banco, URLs cifradas, IA inicialmente desligada;
  usa a reconciliação normal para cadastrar `cam_<id>` no go2rtc da mãe.
- Nome/URL iguais são preservados; colisão de nome com outra origem cancela o lote.
  Não remove câmeras, não altera receitas existentes nem migra o banco.
- Limites: YAML 1 MB, 200 câmeras; mantém intacta a entrada YAML do go2rtc.
- Código: `painel/simulater_dsa.py`, rotas em `routes_cameras.py`, botão na página
  Câmeras e template `_simulater_dsa.html`. Testes em `test_painel_simulater_dsa.py`.
- Os YAMLs reais foram parseados: Jutta 7 câmeras, Perinatal 10. Isso não significa
  que essas câmeras já tenham sido importadas no banco operacional.

Pasta local dos cenários:
`C:\Users\drb-d\Downloads\SimulaterDSA_v0.5.0\rtsp_sim\dist\SimulaterDSA\cenarios`.
Na consulta realizada, Perinatal tinha 10 fontes prontas na API
`http://127.0.0.1:9997/v3/paths/list`. Consulte novamente antes de testar.

O simulador é **MediaMTX + FFmpeg**, não go2rtc. Ele ocupa `127.0.0.1:8554`.
O go2rtc padrão do projeto tenta usar a mesma porta: na mesma máquina é preciso
separá-las e manter `go2rtc_rtsp_url` consistente com o restream da mãe.
O importador não muda portas nem inicia/interrompe o simulador.

## Próximo trabalho, em ordem

1. Conferir go2rtc da mãe: executável/configuração/porta e disponibilidade atual.
   Corrigir a integração local sem parar o simulador nem trocar sua porta por surpresa.
   Se faltar dependência, registrar precisamente qual; não dizer apenas “sem ambiente”.
2. Validar a importação SimulaterDSA pelo painel, preferencialmente em banco isolado,
   e a cadeia normal **MediaMTX → go2rtc da mãe → análise/player**. Evitar desvio
   direto para MediaMTX como substituto de prova dessa cadeia.
3. Fazer um ensaio curto direcionado de vídeo contínuo, overlay e popups em tela
   estreita; registrar URLs/estados e erros concretos. Não repetir ensaio longo
   quando o único problema é uma URL de imagem ou um estado inicial do template.
4. Preservar modo simples: um especialista e uma receita por câmera, núcleo genérico,
   diagnóstico só do mestre, ROI separada das margens de crop. Continuar em sombra.
5. Documentar o resultado nos registros existentes. Encerrar/arquivar apenas quando
   os critérios de aceite estiverem atendidos; 02, 03 e 05 seguem abertas.

Não aumentar o orçamento de 500 ms só para fazer teste passar. Medir antes de
ajustar cadência/prazo por câmera. Os negativos do modelo de bebê não provam que
crop seja a causa: comparar entradas antes de concluir; suporte ao alvo `quadro`
é trabalho separado. Não inferir qualidade, identidade humana ou rótulos confirmados.

Preservar `src/models/Bebe-no-colo.pt`, pesos, vídeos e evidências locais fora do Git.
No retorno, informar só mudanças novas, testes executados e o bloqueio real seguinte.
