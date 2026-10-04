# Revisão visual dos episódios e do avaliador

Data: 2026-10-04. Base: `59de470`. Autor: Codex (IA), com revisão independente do avaliador por um subagente. Estado: proposta de anotação; etapa 02 permanece em andamento.

## Método e alcance

Li os vídeos locais por OpenCV e inspecionei folhas de contato distribuídas por toda a duração: DVR-1 a cada 8 quadros (0,533 s), DVR-3 a cada 15 quadros (0,5 s). Conferi transições adicionais a cada 0,2 s: DVR-1 entre 7 e 10 s; DVR-3 entre 2 e 3 s e entre 9,5 s e o final. Para `stream-normal.mp4`, inspecionei a cada 8 quadros (0,267 s). Foram decodificados 480, 441 e 395 quadros, respectivamente. Decodificar todos não significa inspecionar visualmente todos.

Esta é uma revisão por IA de imagens amostradas, sem áudio, sem confirmação humana e sem observação fora do campo da câmera. Criança/adulto são classificações visuais aproximadas; proximidade não prova parentesco ou responsabilidade. Os alertas existentes foram consultados, portanto esta revisão não é cega. Tempos são relativos ao arquivo, não ao relógio sobreposto.

As folhas estão somente em `runtime/revisao_codex_20261004/`, ignorado pelo Git. Nenhuma imagem de pessoa foi adicionada aos documentos versionáveis. Os três JSON em [propostas_ia](propostas_ia/) conservam `revisado=false` e `revisao_completa_do_video=false`; não são entradas aprovadas para calcular qualidade.

## Achados visuais

| Vídeo / candidato | Evidência e intervalo proposto | Relação com alertas já registrados |
|---|---|---|
| DVR-1 / dvr1-e1 | A criança de roupa vermelha permanece junto à parede. O adulto inicialmente próximo se afasta a partir de aproximadamente 5 s e deixa o campo perto de 9,2 s. Não vejo reunião até o final, 31,93 s. Proponho um episódio contínuo, com início conservador em 9,2 s. | Alertas em 15,667; 21,267; 31,133 s, trilhas 4, 20 e 2. Candidatos a duas repetições do mesmo episódio. |
| DVR-3 / dvr3-e1 | Primeira criança aparece junto às cadeiras à direita por volta de 2,4 s e sai pela borda inferior aproximadamente em 10,8 s. Sem adulto próximo acompanhando nos quadros examinados. | Alertas em 6,333 e 6,567 s, trilhas 1 e 18: candidato a uma repetição. |
| DVR-3 / dvr3-e2 | Outra passagem, criança de roupa clara, aparece junto às cadeiras aproximadamente em 10,4 s e sai perto de 14,0 s. É separável visualmente da primeira passagem, que ainda termina na borda inferior. | Nenhum alerta listado para essa passagem: candidato a episódio perdido. |
| stream-normal.mp4 / negativo | 13,17 s de câmera fixa: corredor vazio no início; depois passa uma pessoa de aparência adulta. Não identifiquei criança nos quadros examinados. | Candidato a controle negativo. Ainda falta executar a análise desse arquivo e confirmar o rótulo. |

O início de DVR-1 depende da definição operacional: afastamento visível ocorre antes de o adulto sair do campo. O valor 9,2 s é um marco conservador, não uma medição da distância permitida pela receita. Deve ser fechado antes de comparar atrasos. Para DVR-1 proponho `qualquer` entre os dois estados de alerta, pois esta anotação agrupa o evento contínuo; não valida a classificação exata do estado.

No DVR-3, os intervalos de duas pessoas podem se sobrepor. O avaliador precisa associar alertas a um sujeito do episódio, ou admitir ambiguidade; tempo e estado sozinhos não resolvem isso. Os identificadores de sujeito dos JSON são locais ao vídeo e não identificam pessoas reais.

Se esses três episódios positivos forem confirmados, os registros sugerem dois episódios com alerta, um sem alerta e três alertas adicionais dentro dos episódios. **Isso é uma leitura provisória, não métricas oficiais de qualidade.** Não atribuí taxa de falsos positivos nem declarei causa comprovada de fragmentação. IDs diferentes são indício; não medem todas as transições da trilha.

## Seleção do negativo

`stream-normal.mp4` é um candidato visual mais adequado que `Corredor um.mp4`: este último, assim como `Saida Norte.mp4` e `setorB.mp4`, exibe marca “Veo”. Trate esses três como possível conteúdo sintético até confirmar a origem; não misture com referência real sem registrar a procedência. A aparência de CCTV de `stream-normal.mp4` também não comprova sua origem: confirmar com o responsável pelos arquivos. Um negativo tão curto é teste de sanidade, não estimativa robusta de falsos alertas por hora.

## Correções necessárias antes das métricas

Revisão de [episodios.py](../../../tools/bateria/episodios.py), com três reproduções sintéticas pelo subagente:

1. **P1 — associação reutiliza o mesmo alerta** (linha 135). Episódios `[0,10]` e `[12,20]`, alerta em `11`, tolerância 2: resultado atual detecta dois episódios com um alerta. `usados` só participa da contagem de falsos. Definir associação exclusiva e determinística, com desempate documentado e tratamento de sujeitos simultâneos.
2. **P1 — revisão sem lista vira negativo** (linha 127). Hash e booleanos verdadeiros, sem `episodios`, revisor ou data, são aceitos e todos os alertas viram falsos. Validar estrutura, tipos, intervalos, IDs e lista obrigatória; `[]` deve ser deliberado. Distinguir proposta por IA de confirmação humana, sem alegar que metadados provam a revisão.
3. **P2 — nome da métrica excede o que mede** (linhas 137–140). Sequência `1 → 2 → 1` resulta em uma “troca”, embora haja duas transições. Renomear para trilhas adicionais ou definir/implementar transições. Não confundir duas crianças com fragmentação de uma.
4. **P1 — análise incompleta não impede avaliação** (linhas 113–147). O avaliador ignora `terminou` e cobertura de quadros. Além disso, `analisar_video` calcula `terminou` depois de `sessao.stop()` (linhas 52–61): timeout pode parecer término normal. Registrar motivo antes de parar, separar EOF de falha/timeout e recusar qualidade sem cobertura integral comprovada. Achado por inspeção; ainda requer regressão de timeout.

## Próximo passo e encerramento

Corrigir o avaliador e seus testes; confirmar a unidade de episódio e seus marcos; revisar os vídeos integralmente com pessoa identificada conforme o procedimento vigente; analisar o negativo confirmado; só então calcular qualidade. Preservar análises anteriores e registrar a versão do avaliador. Não repetir a matriz de capacidade para corrigir o avaliador.

A suíte completa e os dois commits anteriores não foram revalidados nesta revisão. Não houve alteração de código, pesos ou rótulos aprovados. Este arquivo registra a revisão; o [retorno ao Claude](../../retorno_claude_etapa02.md) limita o próximo lote.
