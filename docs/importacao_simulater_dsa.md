# Câmeras de cenário SimulaterDSA

**Atualização final de 05/10/2026:** go2rtc instalado e cadeia real validada.
O relato de ausência no fim deste documento é histórico. Estado atual e evidências:
[`fechamento_integracao_go2rtc.md`](fechamento_integracao_go2rtc.md).

Em **Câmeras → SimulaterDSA — importar cenário** (administrador), informe o host
do simulador visto pelo servidor Jutta, selecione o YAML e clique em
**Cadastrar câmeras do cenário**. O padrão local é `127.0.0.1`.

O importador aceita cenários `version: 1`, lê `server.rtsp_port` (padrão 8554)
e `sources[].cameras` das fontes habilitadas. Cada câmera vira uma fonte
RTSP genérica persistida no banco, cifrada, com IA desligada. O sincronizador
normal publica essas fontes no go2rtc da mãe como `cam_<id>`. Não há alteração
no importador de nomes de streams do YAML go2rtc nem necessidade de migrar o banco.

Reimportar nome e URL iguais preserva a câmera, seus ajustes e associações.
Um nome existente com origem diferente cancela a importação inteira.
Fontes desabilitadas são ignoradas; câmeras existentes não são removidas.
O lote tem limite de 200 câmeras e o YAML de 1 MB. O upload exige CSRF,
autorização de administrador e registra auditoria da importação concluída.

O YAML é configuração, não conteúdo executável: `path` não é aberto, copiado
ou executado. O cenário precisa estar iniciado no SimulaterDSA.
Se go2rtc estiver indisponível, os cadastros ficam pendentes para a reconciliação.
Cadastrar não comprova chegada de vídeo, inferência nem qualidade de modelo.

Na mesma máquina, MediaMTX e go2rtc precisam de portas RTSP diferentes: com o simulador em `8554`, defina em
**Configurações** a *Base RTSP de retransmissão* como `rtsp://127.0.0.1:8556`. O YAML base do go2rtc, gerado na primeira
execução, passa a escutar nessa porta (um `go2rtc.yaml` já existente não é sobrescrito: ajuste `rtsp: listen` nele). Se a porta
já estiver em uso, o painel não sobe o go2rtc e **Processamento** mostra o motivo e a porta sugerida.
O importador não muda portas nem reinicia serviços. Mantenha a porta do
simulador na URL de origem; a porta de restream do go2rtc deve coincidir com
`go2rtc_rtsp_url` nas configurações da mãe.

Testes: `tests/test_painel_simulater_dsa.py` cobre importação e sincronização
com go2rtc simulado, repetição, conflito atômico, entradas inválidas, CSRF e
permissões. A revisão visual e o vídeo no navegador ainda devem ser executados
com o painel e o go2rtc reais em funcionamento.

Validação em 05/10/2026 (banco isolado, `perinatal.yaml` real, go2rtc ausente): 10 câmeras cadastradas, URLs cifradas, reimportação
idempotente, sincronização `pendente` exibida com honestidade. A cadeia completa até o go2rtc continua dependendo do executável do go2rtc
(não instalado nesta máquina). Ver `docs/etapas_motor/05f_configuracao_camera.md`.
