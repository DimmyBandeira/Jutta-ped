# Fechamento das cinco pendências da etapa 02

Base revisada: `d16dab6`. Data: 2026-10-04. Preparado pelo Codex, sem confirmação humana em nome do usuário.

## Onde estamos

Reexecutei `tests/test_bateria_episodios.py`: **100 passaram**. Conferi os contratos de anotação, confirmação e cobertura e os registros da rodada. Não reexecutei os 651 testes nem a inferência. Capacidade já foi medida; resta fechar a referência de qualidade antes da comparação da migração. Esta revisão não certifica ausência de outros defeitos.

Os JSON desta pasta são derivados das propostas originais, com critérios de início preenchidos e revisão explicitamente não cega. Continuam como propostas de IA, sem confirmação humana. `leituras_provisorias.json` foi calculado usando as análises v2 existentes; não é resultado oficial. Não substitui os relatórios históricos.

| Pendência | Resolução nesta rodada | O que ainda exige informação externa |
|---|---|---|
| 1. Pessoa confirma | Três rascunhos preparados, vídeos e roteiro abaixo; nenhuma declaração fabricada | Nome de quem assistiu integralmente, confirmação/correção dos episódios e dos intervalos |
| 2. Critério de início | **Definido para esta referência:** DVR-1 em 9,2 s; DVR-3 em 2,4 s e 10,4 s, com critérios abaixo | Revisor confere os marcos no vídeo integral e corrige se necessário |
| 3. Procedência | DVR-1 corresponde exatamente a um DAV do ZIP local; evidência e hashes registrados | Responsável confirma fonte e natureza dos três vídeos; DVR-3 e stream-normal sem cadeia local adicional encontrada |
| 4. Revisão cega | **Resolvido: não cega**, `revisao_cega=false` | Nenhuma declaração de cegamento retroativo |
| 5. Negativo longo | Inventário feito; protocolo de aquisição definido; curto mantido como sanidade | Falta gravação real mais longa, representativa e integralmente revisada |

## Critérios fixados para a comparação

- **DVR-1 / dvr1-e1:** início em **9,2 s**, quando o adulto antes próximo já saiu completamente do campo e a criança permanece visível; final em 31,933 s, limite do arquivo, não resolução do evento. A separação espacial começa antes, perto de 5 s. O atraso calculado a partir de 9,2 s deve se chamar atraso após saída do adulto do campo. Não pode sustentar conclusão sobre resposta desde o afastamento nem redefinir o prazo de segurança da receita. Se futuramente medir afastamento, fazê-lo como outra medida, sem trocar silenciosamente esta referência.
- **DVR-3 / dvr3-e1:** início em **2,4 s**, primeira aparição identificável da primeira criança junto às cadeiras à direita, sem acompanhamento próximo visível; final proposto em 10,8 s.
- **DVR-3 / dvr3-e2:** início em **10,4 s**, aparição identificável de outra criança na mesma região; final proposto em 14,0 s. Sujeitos locais diferentes, mesmo havendo sobreposição de intervalos.

Os instantes vieram de inspeção amostrada e são marcos aproximados, não precisão quadro a quadro. O revisor humano pode corrigir os rascunhos; deve fazê-lo antes de congelar a referência usada pelo motor novo. Nenhum marco foi escolhido por proximidade de um alerta.

Esta rodada é uma referência de regressão de engenharia **não cega**: já conhecemos os alertas. Para avaliação independente futura, outra pessoa deve anotar vídeos novos sem consultar resultados ou propostas antes de congelar os rótulos. Não é preciso fingir cegamento para concluir esta rodada.

## Conferência humana em cerca de um minuto de vídeo

Assista integralmente aos originais, nesta ordem (aproximadamente 60 s no total):

1. [DVR-1, 32 s](../../../../videos/DVR-1-Ch.7-8%205--%20PAV-Hall-ELEV%20SERVI.dav): conferir episódio contínuo, saída do adulto e ausência de reencontro até o final. Rascunho: [dvr1.json](dvr1.json).
2. [DVR-3, 14,7 s](../../../../videos/DVR-3-Ch.0-1%20ACESSO-BLINDEX.avi): conferir duas passagens distintas e seus intervalos. Rascunho: [dvr3.json](dvr3.json).
3. [Stream normal, 13,17 s](../../../../videos/stream-normal.mp4): confirmar se todo o vídeo é negativo. Rascunho: [negativo_stream_normal.json](negativo_stream_normal.json).

Responder: nome do revisor; vídeos vistos integralmente; episódios/intervalos aceitos ou correções; fonte e natureza real/sintética de cada arquivo; responsável pela origem quando diferente do revisor. O campo de confirmação da CLI presume o mesmo responsável: se forem pessoas diferentes, registrar fielmente no JSON e validar.

Após receber essas declarações, aplicar `confirmar` aos rascunhos desta pasta conforme [procedimento](../../README.md), sem `--revisao-cega`, e executar `reavaliar`. Não rodar flags de confirmação antecipadamente. As propostas em `../propostas_ia/` permanecem intactas.

## Evidência de procedência e inventário

O SHA-256 `63bf6a2912e55e3dd44ae858ba234ac78dbcf72e01818eb924f6bd94e8027dd9` coincide entre o DVR-1 usado na referência e a entrada `5° ANDAR/DVR 1-Ch.7-8 5-- PAV Hall ELEV SERVI--O-2026-06-09-16-05-30.dav` do ZIP. Isso comprova correspondência de bytes, não autoria, instituição ou autorização. O relatório de 19/06 descreve os dois DVR como vídeos reais; a data do relatório não é a data de captura do DVR-1.

[inventario_zip.json](inventario_zip.json) registra entradas, tamanhos, hashes e duração dos AVI. Há 11 AVI, de 14 a 30 s, somando 210 s, além de DAV possivelmente correspondentes. Não contar AVI e DAV como exposições independentes. O clipe mais longo solto em `videos/` é `People-wold.mp4`, com 116,55 s: também não atende 600 s e não foi aprovado como negativo. Não foi encontrado negativo longo no material local inspecionado.

## Como obter o negativo que falta

Exportar **ao menos 10 minutos contínuos novos** da câmera e do contexto de uso pretendido, com horário de início/fim e responsável registrados, antes de consultar os alertas. Este mínimo apenas atende o limiar de vídeo curto do avaliador; não garante precisão estatística ou taxa baixa de falsos alertas.

Preferir circulação normal de adultos e, quando disponível, crianças acompanhadas. Uma cena vazia sozinha exercita pouco os erros de classificação. Revisar todo o trecho: se houver um episódio positivo, anotá-lo honestamente e buscar outro negativo; não removê-lo para produzir um resultado melhor. Preservar resultados com falsos alertas. Não repetir, concatenar cópias ou desacelerar os 13 s existentes para fabricar duração. Medir alertas por hora usando a duração real revisada, sempre informando contagem e exposição; ampliar a coleta antes de estabelecer taxa operacional.

Esse arquivo precisa vir de uma gravação disponível ao responsável pelo projeto. Não há fonte RTSP nem exportação longa identificada nesta sessão. A coleta não foi executada nem pode ser declarada concluída.

## Continuidade

O usuário informou nesta conversa que já assistiu aos vídeos. Não informou nome do revisor, correções/confirmação dos intervalos ou origem dos arquivos; essas declarações não foram inferidas. Também reafirmou o foco no sistema-mãe e nos recortes. O [próximo lote](../../../retorno_claude_motor_minimo.md) prioriza essa integração em sombra, sem esperar um negativo longo para começar a construção.

Os itens 2 e 4 estão definidos. Os itens 1, 3 e 5 dependem das declarações e do arquivo descritos acima. A etapa 02 continua em andamento; não arquivar seu registro nem iniciar comparação oficial da migração com rótulos provisórios. Enquanto isso, desenho das receitas pode avançar sem alterar a referência nem depender de métricas inventadas.
