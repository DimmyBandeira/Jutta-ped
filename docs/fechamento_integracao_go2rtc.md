# Fechamento da frente SimulaterDSA → go2rtc → painel

**Estado: frente de integração local concluída em 05/10/2026.** Este é o ponto
de entrada para um novo chat. Substitui os relatos anteriores de que não havia
go2rtc na máquina. Não declara a migração pediátrica nem a qualidade concluídas.

## O que foi entregue

- Importação administrativa de cenários SimulaterDSA pelo painel, com câmeras
  RTSP cifradas, IA desligada por padrão, CSRF, auditoria e reimportação idempotente.
- YAML inicial do go2rtc usa API/Base RTSP configuradas; porta ocupada produz erro
  explícito. Processamento mostra estado e motivo do processo.
- go2rtc **v1.9.14 Windows amd64** instalado em `runtime/go2rtc/go2rtc.exe` a partir
  da [release oficial](https://github.com/AlexxIT/go2rtc/releases/tag/v1.9.14).
  ZIP SHA-256 conferido com o digest da API GitHub:
  `dd4167d75cb04abe618855b7c71f8658bd009f60c1a71835d134d2c11c939907`.
  Executável SHA-256:
  `923d57252e8139a69c52e4acc1e399a640244a8ef457fd9b7267a25847d68f8c`.
- Banco local `runtime/painel.db` configurado com Base RTSP
  `rtsp://127.0.0.1:8556`; backup consistente anterior em
  `runtime/painel_antes_go2rtc_8556.db`. Alteração registrada em auditoria como `codex`.
  YAML base local criado com a mesma porta. As outras configurações e câmeras
  operacionais foram preservadas.
- O simulador continua em **8554** (MediaMTX) e **9997** (API), sem alteração ou parada.
  O go2rtc usa **1984** (API/player), **8556** (RTSP) e **8555** (WebRTC).

O binário, ZIP, banco, chaves, vídeos e evidências ficam em `runtime/` ou pastas
locais ignoradas pelo Git. Um clone novo não inclui o executável: precisa instalá-lo.

## Evidência executada

Roteiro reproduzível: `tools/validar_cadeia_go2rtc.py --cenario <perinatal.yaml>`.
Usa banco isolado, servidor HTTP real, go2rtc iniciado pela mãe, modelos OpenVINO
reais em CPU, runtime real e Edge headless. Recusa iniciar se 1984/8556 estiverem
ocupadas; não toma controle de outro serviço. Não é benchmark.

Resultado aprovado salvo localmente em
`runtime/validacao_go2rtc_20261005_140615/resultado.json`:

| Verificação | Resultado |
|---|---|
| Upload pelo painel do `perinatal.yaml` real | 10 câmeras importadas, todas sincronizadas e inicialmente sem IA |
| Habilitação de uma câmera pelo formulário | Runtime analisou o restream `rtsp://127.0.0.1:8556/cam_1` |
| Partida da análise | 6 quadros processados no primeiro ponto de conferência |
| Diagnóstico do mestre | 13 cenas; nenhuma vazia/recusada nessa amostra |
| Player do go2rtc dentro do painel | 1280 × 720; relógio avançou de 0,010588 para 4,017077 s |
| Overlay do runtime real | Canvas recebeu desenho sobre o player |
| Tela 390 × 844 | Sem rolagem horizontal; três popups recebidos por SSE; “Fechar todos” funcionou |
| JavaScript | Nenhuma exceção capturada |
| Consumidores no go2rtc para a câmera | 2 no ponto de consulta (análise e player) |
| Simulador ao final | 10 fontes prontas |

**Popups foram sintéticos**, com imagens de cores sólidas, para exercitar a
interface sem depender de ocorrer um alerta verdadeiro. O vídeo, a análise e
o overlay eram reais. A captura local `mobile.png` foi inspecionada: os três
cartões ocupam grande parte da tela; o botão de dispensar todos permanece acessível.
A captura ocorreu durante a animação de entrada; o roteiro passou a aguardar
300 ms antes de novas capturas. Não se afirma medição de alinhamento/latência do overlay.

Uma tentativa inicial falhou porque o roteiro reconciliava antes de terminar o
PUT de salvar a câmera; corrigiu-se a sincronização do roteiro para esperar a
resposta HTTP. A execução seguinte passou. Não foi atribuído isso ao produto.

Após o ensaio, suas sessões, servidor temporário e processo go2rtc foram encerrados.
O executável permanece instalado; o painel normal o inicia no seu ciclo de vida.
As dez câmeras de teste ficaram **no banco isolado**, não foram inseridas no banco
operacional. Para uso normal, importar o cenário pela entrada SimulaterDSA existente.

**Suíte final:** 1.315 passaram, 9 pulados, 1 aviso de depreciação Starlette/httpx,
em 197,27 s. Os 9 pulados foram identificados: três testes de fumaça dependem
de caminhos locais de vídeo/modelo ausentes; seis testes antigos de navegador
exigem o alias `entrada_social_vis`, ausente. O ensaio real desta frente usa o
cenário Perinatal e `cam_1`, portanto foi executado separadamente e aprovado.
Não foram inventados aliases para transformar os testes antigos em evidência
de uma fonte diferente. A bateria de capacidade não foi repetida.

## O que fica para novas demandas

Esta frente não deve voltar a ser descrita como bloqueada por falta de go2rtc.
Os registros de 03/05f continuam válidos para contratos e limitações do motor.
Permanecem separados deste fechamento:

- Etapa 02: confirmação humana/procedência e referência de qualidade.
- Migração pediátrica, regras legadas ainda não reproduzidas e ativação de alertas reais.
- Calibração de cadência/orçamento sob carga; 500 ms não foi ampliado para aprovar teste.
- Comparação quadro inteiro × crop no modelo de bebê e investigação de pessoas
  não detectadas. Não concluir causas a partir de uma execução.
- Validação do especialista genérico pela mesma cadeia com múltiplas câmeras:
  este ensaio usou uma câmera com o runtime operacional e diagnóstico. O relato
  anterior de especialistas em três câmeras usava MediaMTX direto.
- Etapa 04 (executor/fila), nós remotos e térmico, que não foram implementados aqui.

Não encerrar automaticamente 02, 03 ou 05, nem arquivar o registro 05f enquanto
seus demais critérios estiverem em aberto. Para nova demanda, ler este arquivo
e `docs/plano_motor_plugins.md`; consultar históricos apenas quando necessário.
