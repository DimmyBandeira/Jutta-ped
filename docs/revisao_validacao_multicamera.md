# Revisão do ensaio multicâmera — 05/10/2026

Parecer: avanço funcional coerente com a mãe genérica e especialistas empacotados.
O pacote real em A/B e o pacote determinístico em C estão explicitamente separados.
**A evidência funcional é útil; as medições não sustentam ainda uma decisão de capacidade.**
Reexecutei 26 testes (dispositivo, multicâmera e corridas da sombra): todos passaram.
Não repeti a suíte completa nem a rodada RTSP. Li o resultado local
`runtime/validacao_motor_20261005_144432/resultado.json` e o roteiro.

## Achados na medição

1. **Janela sem limite final.** Em `tools/validar_motor_especialistas.py`, chamadas
   (linha 431) e eventos (linha 438) são filtrados apenas por `>= t_ini`, depois de
   parar as sessões em sequência. Trabalho finalizado durante a parada entra nas
   estatísticas, mas o denominador de cadência termina em `t_fim`. Limitar a janela
   pelos dois extremos, registrar os timestamps e separar resultados de drenagem.
   Definir também se a população é por captura, início de execução ou conclusão.

2. **Partida não comprova aquecimento completo.** `t_ini` é fixado imediatamente
   depois de `start_session` das três câmeras (linha 415). O método inicia a thread
   de análise e retorna, sem esperar sua primeira inferência. Aquecer previamente
   o pacote não aquece os detectores/classificadores próprios das três sessões.
   Aguardar atividade/aquecimento observável de todas antes da janela, com prazo
   máximo e falha explícita se alguma não ficar pronta. Não afirmar que todos os
   estouros anteriores eram aquecimento só por terem diminuído.

3. **Contadores de períodos diferentes.** Os quadros lidos/processados, descartes
   e despacho são acumulados desde a partida; eventos/latências pretendem medir
   60 s. O JSON identifica alguns como totais, mas essa população não serve para
   derivar fps/descarte da janela comum. Registrar snapshots no início/fim e seus
   deltas, mantendo totais separados. Eventos por segundo não são cenas por segundo:
   contrato 1 pode emitir por pessoa, contrato 2 por cena.

4. **Dispositivo solicitado não comprova o executado.** As sessões pedem CPU,
   enquanto o pacote real prefere `intel_gpu` e o JSON registra `intel:gpu`.
   Na biblioteca instalada, o backend OpenVINO pode cair silenciosamente para
   `AUTO` se não encontrar o nome solicitado. A consulta desta revisão retornou
   `CPU`, `GPU.0`, `GPU.1`; isso não reconstitui o hardware usado na rodada anterior.
   Registrar dispositivo solicitado, fallback e `EXECUTION_DEVICES` do modelo
   efetivamente compilado após a inferência, inclusive por modelo de sessão.
   Não rotular essa rodada como integralmente CPU ou GPU Intel comprovada.

Esses pontos afetam as conclusões quantitativas, não invalidam por si a passagem
real dos crops pelos pacotes nem os testes determinísticos de isolamento.

## Retorno para o Claude

Corrija a instrumentação acima antes de usar esses números para justificar etapa 04.
Acrescente testes determinísticos da janela (antes, dentro e depois), contadores
por delta e distinção de cenas/eventos. Preserve o relatório anterior; marque-o
como evidência funcional com limitações quantitativas. Uma rodada curta após as
correções basta para conferir a instrumentação; não iniciar outra campanha longa.

Não alterar o orçamento para obter aprovação. As cadências A=0,4 s, B=1 s e
C=0,4 s já foram declaradas na rodada atual: a próxima execução deve ter uma
hipótese explícita, não apenas “rodar com cadência declarada”.

A etapa 02 é requisito para avaliar qualidade/equivalência da migração pediátrica,
não pré-requisito para continuar o sistema-mãe genérico. Depois dessa correção,
conferir os critérios remanescentes do 05f e preparar o desenvolvimento de um
especialista em workspace separado, usando só o contrato público, molde e validador.
Não iniciar nós, treinamento ou executor compartilhado por inferência destes dados.

Manter alterações sem commit nesta revisão; registrar testes e limites com precisão.
